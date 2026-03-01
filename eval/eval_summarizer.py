"""
MindCache Evaluation — RAPTOR Summarizer (S1-S5)

Validates that the RecursiveSummarizer (nodes_summary.py) produces
correct source_maps and ignored_ids without hallucinating IDs.

S1: Source Map ID Validity  — are all IDs in source_map real children?
S2: Ignored ID Validity     — are all ignored IDs real children?
S3: Coverage Rate           — are all children accounted for?
S4: Summary Faithfulness    — does each source_map entry match child content?
S5: Ignore Justice          — are ignored children truly low-value?

Usage:
    python eval/eval_summarizer.py
    python eval/eval_summarizer.py --save
"""
import sys
import os
import json
import argparse
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from eval.metrics import (
    source_map_id_validity,
    ignored_id_validity,
    coverage_rate,
    cosine_similarity
)


# ─── Embedding Helper ────────────────────────────────────────────
_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder

def embed_fn(texts: List[str]):
    import numpy as np
    model = get_embedder()
    if not texts:
        return np.array([])
    return model.encode(texts, convert_to_numpy=True)


# ─── S4: Summary Faithfulness ────────────────────────────────────
def check_summary_faithfulness(
    source_map: Dict[str, str],
    children: List[Dict],
    threshold: float = 0.5
) -> Dict:
    """
    For each source_map entry, check if the summary text is faithful
    to the child's actual description/memories.
    """
    if not source_map or not children:
        return {"faithfulness_rate": 1.0, "checked": 0, "unfaithful": []}
    
    child_map = {str(c["id"]): c for c in children}
    faithful = 0
    unfaithful_items = []
    checked = 0
    
    for child_id_str, sm_summary in source_map.items():
        child = child_map.get(child_id_str)
        if not child:
            continue  # Phantom ID, already caught by S1
        
        child_desc = child.get("description", "")
        if not child_desc or not sm_summary:
            continue
        
        checked += 1
        vecs = embed_fn([sm_summary, child_desc])
        sim = cosine_similarity(vecs[0], vecs[1])
        
        if sim >= threshold:
            faithful += 1
        else:
            unfaithful_items.append({
                "child_id": child_id_str,
                "child_name": child.get("name", ""),
                "source_map_summary": sm_summary,
                "child_description": child_desc[:200],
                "similarity": float(sim)
            })
    
    return {
        "faithfulness_rate": faithful / checked if checked > 0 else 1.0,
        "checked": checked,
        "faithful_count": faithful,
        "unfaithful": unfaithful_items
    }


# ─── S5: Ignore Justice ──────────────────────────────────────────
def check_ignore_justice(
    ignored_ids: List,
    children: List[Dict],
    memory_threshold: int = 5
) -> Dict:
    """
    Check if ignored children are truly low-value.
    Flag any ignored child with more than `memory_threshold` memories.
    """
    child_map = {c["id"]: c for c in children}
    unjust = []
    checked = 0
    
    for ignored_id in ignored_ids:
        try:
            iid = int(ignored_id)
        except (ValueError, TypeError):
            continue
        
        child = child_map.get(iid)
        if not child:
            continue
        
        checked += 1
        mem_count = child.get("memory_count", 0)
        
        if mem_count > memory_threshold:
            unjust.append({
                "child_id": iid,
                "child_name": child.get("name", ""),
                "memory_count": mem_count,
                "description": child.get("description", "")[:200]
            })
    
    return {
        "justice_rate": 1.0 - (len(unjust) / checked if checked > 0 else 0.0),
        "checked": checked,
        "unjust_ignores": unjust
    }


# ─── Main Evaluation ─────────────────────────────────────────────
def run_summarizer_eval(save_results: bool = False) -> Dict:
    """
    Scan all parent topics in the DB and validate their source_maps.
    """
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Get all parent topics (topics with children)
        all_topics = session.query(Topic).all()
        parent_topics = [t for t in all_topics if t.children]
        
        print(f"[Eval] Found {len(parent_topics)} parent topics to evaluate")
        
        if not parent_topics:
            print("[Eval] No parent topics with source_maps found. Need to run summarizer first.")
            return {"error": "No parent topics found"}
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "num_parents": len(parent_topics),
            "s1_source_map_validity": [],
            "s2_ignored_id_validity": [],
            "s3_coverage": [],
            "s4_faithfulness": [],
            "s5_ignore_justice": [],
        }
        
        for topic in parent_topics:
            print(f"\n--- Topic: '{topic.name}' (ID={topic.id}, Level={topic.level}) ---")
            
            child_ids = [c.id for c in topic.children]
            
            # Parse source_map state from summary
            state = {}
            if topic.summary:
                try:
                    parsed = json.loads(topic.summary)
                    if isinstance(parsed, dict) and ("source_map" in parsed or "ignored_ids" in parsed):
                        state = parsed
                except:
                    pass
            
            sm = state.get("source_map", {})
            ignored = state.get("ignored_ids", [])
            
            if not sm and not ignored:
                print(f"  [SKIP] No source_map or ignored_ids yet")
                continue
            
            # S1: Source Map ID Validity
            s1 = source_map_id_validity(sm, child_ids)
            results["s1_source_map_validity"].append(s1)
            print(f"  [S1] ID Validity: {s1['validity_rate']:.2f} "
                  f"({s1['valid_count']}/{s1['total_in_map']} valid, "
                  f"{s1['phantom_count']} phantom)")
            
            # S2: Ignored ID Validity
            s2 = ignored_id_validity(ignored, child_ids)
            results["s2_ignored_id_validity"].append(s2)
            print(f"  [S2] Ignored Validity: {s2['validity_rate']:.2f} "
                  f"({s2['valid_count']}/{s2['total_ignored']} valid)")
            
            # S3: Coverage
            s3 = coverage_rate(sm, ignored, child_ids)
            results["s3_coverage"].append(s3)
            print(f"  [S3] Coverage: {s3['coverage_rate']:.2f} "
                  f"({s3['covered_count']}/{s3['total_children']})")
            
            # S4: Faithfulness (needs child descriptions)
            children_data = []
            for child in topic.children:
                # Count memories for S5
                mem_count = 0
                for Model in [UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory]:
                    mem_count += session.query(Model).filter(Model.topic_id == child.id).count()
                
                children_data.append({
                    "id": child.id,
                    "name": child.name,
                    "description": child.description or "",
                    "memory_count": mem_count
                })
            
            s4 = check_summary_faithfulness(sm, children_data)
            results["s4_faithfulness"].append(s4)
            print(f"  [S4] Faithfulness: {s4['faithfulness_rate']:.2f} "
                  f"({s4['faithful_count']}/{s4['checked']} faithful)")
            
            # S5: Ignore Justice
            s5 = check_ignore_justice(ignored, children_data)
            results["s5_ignore_justice"].append(s5)
            print(f"  [S5] Justice: {s5['justice_rate']:.2f} "
                  f"({len(s5['unjust_ignores'])} unjust ignores)")
        
        # ─── Aggregate ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("AGGREGATE RESULTS (RAPTOR Summarizer)")
        print("=" * 60)
        
        for metric_name, metric_key, rate_key in [
            ("S1 Source Map ID Validity", "s1_source_map_validity", "validity_rate"),
            ("S2 Ignored ID Validity", "s2_ignored_id_validity", "validity_rate"),
            ("S3 Coverage Rate", "s3_coverage", "coverage_rate"),
            ("S4 Summary Faithfulness", "s4_faithfulness", "faithfulness_rate"),
            ("S5 Ignore Justice", "s5_ignore_justice", "justice_rate"),
        ]:
            values = [r[rate_key] for r in results[metric_key]]
            if values:
                avg = sum(values) / len(values)
                print(f"  {metric_name}: {avg:.3f} (n={len(values)})")
            else:
                print(f"  {metric_name}: N/A (no data)")
        
        # Save
        if save_results:
            out_path = f"eval/results/summarizer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            serializable = json.loads(json.dumps(results, default=str))
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            print(f"\nResults saved to: {out_path}")
        
        return results
    
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate RAPTOR Summarizer")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    
    run_summarizer_eval(save_results=args.save)
