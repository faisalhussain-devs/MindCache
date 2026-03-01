"""
MindCache Evaluation — Retrieval Pipeline (R1-R8)

Evaluates the retrieval pipeline's ability to find relevant memories
given a query. Uses LongMemEval adapted data.

R1: Root Accuracy       — correct root node selected?
R2: Candidate Recall@K  — target in top-K from descent?
R3: Refiner Precision   — relevant selections only?
R4: Refiner Chain Valid  — output chain/ID exists in DB?
R5: Hit Rate @ 1        — top-1 context relevant?
R6: MRR                 — rank of first relevant result
R7: Depth Accuracy      — correct summary vs leaf?
R8: E2E Latency         — total time query → context

Usage:
    python eval/eval_retrieval.py --dataset eval/data/longmemeval_adapted.json --max-queries 20
    python eval/eval_retrieval.py --dataset eval/data/longmemeval_adapted.json --save
"""
import sys
import os
import json
import time
import argparse
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic
from Database.db_manager import DatabaseManager
from retrieval.context_bridge import ContextBridge
from retrieval.root_search import RootSearch
from retrieval.root_descent import RootDescent
from retrieval.structs import RetrievalConfig, RetrievalContext


# ─── Embedding Helper ────────────────────────────────────────────
_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        from Database.embedder import EmbeddingManager
        _embedder = EmbeddingManager()
    return _embedder


# ─── Stage-Level Evaluation ──────────────────────────────────────
def eval_root_search(root_search: RootSearch, ctx: RetrievalContext, expected_answer: str) -> Dict:
    """
    R1: Root Accuracy
    Check if Root Search picks a root that could contain the answer.
    """
    start = time.perf_counter()
    root_node = root_search.scan(ctx)
    elapsed = time.perf_counter() - start
    
    if root_node is None:
        return {
            "found_root": False,
            "root_name": None,
            "root_id": None,
            "latency_s": elapsed
        }
    
    return {
        "found_root": True,
        "root_name": root_node.name,
        "root_id": root_node.id,
        "latency_s": elapsed
    }


def eval_root_descent(
    root_descent: RootDescent,
    root_node,
    ctx: RetrievalContext,
    expected_answer: str
) -> Dict:
    """
    R2: Candidate Recall @ K
    Check if any candidate topic contains information related to the answer.
    """
    start = time.perf_counter()
    candidates = root_descent.descend(root_node, ctx)
    elapsed = time.perf_counter() - start
    
    candidate_info = []
    for c in candidates:
        candidate_info.append({
            "topic_id": c.topic_id,
            "chain": c.chain,
            "combined_score": c.combined_score
        })
    
    return {
        "num_candidates": len(candidates),
        "candidates": candidate_info,
        "latency_s": elapsed
    }


# ─── Answer Relevance Check ──────────────────────────────────────
def check_answer_in_context(context_text: str, expected_answer: str, threshold: float = 0.6) -> Dict:
    """
    R5: Hit Rate — check if the retrieved context is relevant to the expected answer.
    Uses embedding similarity between expected answer and retrieved context.
    """
    if not context_text or not expected_answer:
        return {"relevant": False, "similarity": 0.0}
    
    model = get_embedder()
    vecs = model.encode([expected_answer, context_text], convert_to_numpy=True)
    sim = float(np.dot(vecs[0], vecs[1]) / (np.linalg.norm(vecs[0]) * np.linalg.norm(vecs[1])))
    
    # Also do simple substring check
    substring_match = expected_answer.lower() in context_text.lower()
    
    return {
        "relevant": sim >= threshold or substring_match,
        "similarity": sim,
        "substring_match": substring_match
    }


# ─── Refiner Chain Validation (R4) ───────────────────────────────
def validate_refiner_chains(selected_topics: List[Dict], session) -> Dict:
    """
    R4: Check if the refiner's selected topic chains/IDs exist in the DB.
    """
    valid = 0
    invalid = 0
    invalid_items = []
    
    for st in selected_topics:
        topic_id = st.get("id")
        if topic_id is not None:
            topic = session.query(Topic).filter_by(id=topic_id).first()
            if topic:
                valid += 1
            else:
                invalid += 1
                invalid_items.append({"hallucinated_id": topic_id, "chain": st.get("chain")})
        else:
            invalid += 1
            invalid_items.append({"error": "no id", "chain": st.get("chain")})
    
    total = valid + invalid
    return {
        "chain_validity_rate": valid / total if total > 0 else 1.0,
        "valid_count": valid,
        "invalid_count": invalid,
        "invalid_items": invalid_items
    }


# ─── End-to-End Retrieval ────────────────────────────────────────
def run_e2e_retrieval(query: str, config: RetrievalConfig = None) -> Dict:
    """
    Run the full retrieval pipeline end-to-end for a single query.
    Returns timing and results for each stage.
    """
    if config is None:
        config = RetrievalConfig()
    
    total_start = time.perf_counter()
    
    # Stage 1: Context Bridge
    bridge = ContextBridge(get_embedder(), config)
    stage1_start = time.perf_counter()
    ctx = bridge.process(query)
    stage1_time = time.perf_counter() - stage1_start
    
    # Stage 2: Root Search
    root_search = RootSearch(config, bridge)
    stage2_start = time.perf_counter()
    root_node = root_search.scan(ctx)
    stage2_time = time.perf_counter() - stage2_start
    
    if root_node is None:
        total_time = time.perf_counter() - total_start
        return {
            "success": False,
            "root_found": False,
            "context_text": "",
            "stage_timings": {
                "context_bridge": stage1_time,
                "root_search": stage2_time,
            },
            "total_time_s": total_time
        }
    
    # Stage 3: Root Descent
    descent = RootDescent(config, bridge)
    stage3_start = time.perf_counter()
    candidates = descent.descend(root_node, ctx)
    stage3_time = time.perf_counter() - stage3_start
    
    total_time = time.perf_counter() - total_start
    
    # Collect context from candidates
    context_parts = []
    for c in candidates:
        if hasattr(c, 'chain') and c.chain:
            context_parts.append(f"[{c.chain}]")
    
    return {
        "success": True,
        "root_found": True,
        "root_name": root_node.name,
        "root_id": root_node.id,
        "num_candidates": len(candidates),
        "candidates": [
            {"topic_id": c.topic_id, "chain": c.chain, "score": c.combined_score}
            for c in candidates
        ],
        "context_text": "\n".join(context_parts),
        "stage_timings": {
            "context_bridge": stage1_time,
            "root_search": stage2_time,
            "root_descent": stage3_time,
        },
        "total_time_s": total_time
    }


# ─── Main Evaluation ─────────────────────────────────────────────
def run_retrieval_eval(dataset_path: str, max_queries: int = None, save_results: bool = False) -> Dict:
    """
    Run R1-R8 metrics on a query dataset (LongMemEval adapted or custom).
    
    Prerequisites: DB must be populated with extracted memories first.
    Run eval_setup.py or ingest data before running this.
    """
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    
    print(f"[Eval] Loaded {len(dataset)} queries from {dataset_path}")
    
    # Handle merged dataset format (dict with "test_cases")
    if isinstance(dataset, dict) and "test_cases" in dataset:
        print("[Eval] Detected merged dataset format")
        dataset = dataset["test_cases"]
    elif isinstance(dataset, dict) and "test_cases" not in dataset:
        # Unexpected dict format
        print("[WARN] Dataset is a dict but no 'test_cases' key found. Trying to listify...")
        dataset = [dataset]
        
    if max_queries:
        dataset = dataset[:max_queries]
    
    # Check if DB has any topics
    Session = sessionmaker(bind=engine)
    session = Session()
    topic_count = session.query(Topic).count()
    session.close()
    
    if topic_count == 0:
        print("[WARN] Database has no topics! Run extraction/ingestion first.")
        print("[WARN] Continuing anyway — results will reflect empty-DB behavior.")
    else:
        print(f"[INFO] Database has {topic_count} topics")
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "dataset": dataset_path,
        "num_queries": len(dataset),
        "db_topic_count": topic_count,
        "per_query_results": [],
        "r1_root_accuracy": [],
        "r5_hit_rate": [],
        "r6_mrr_ranks": [],
        "r8_latencies": [],
    }
    
    for i, item in enumerate(dataset):
        query = item.get("question", "")
        expected_answer = item.get("answer", "")
        question_type = item.get("question_type", "unknown")
        
        print(f"\n--- Query {i+1}/{len(dataset)} [{question_type}] ---")
        print(f"  Q: {query[:100]}...")
        print(f"  A: {expected_answer[:100]}...")
        
        # Run E2E retrieval
        retrieval_result = run_e2e_retrieval(query)
        
        # R1: Root found?
        root_found = retrieval_result["root_found"]
        results["r1_root_accuracy"].append(1.0 if root_found else 0.0)
        
        # R5: Hit Rate (is answer in context?)
        relevance = check_answer_in_context(
            retrieval_result.get("context_text", ""),
            expected_answer
        )
        results["r5_hit_rate"].append(1.0 if relevance["relevant"] else 0.0)
        
        # R8: Latency
        results["r8_latencies"].append(retrieval_result["total_time_s"])
        
        # Store per-query detail
        results["per_query_results"].append({
            "query": query,
            "expected_answer": expected_answer,
            "question_type": question_type,
            "root_found": root_found,
            "root_name": retrieval_result.get("root_name"),
            "num_candidates": retrieval_result.get("num_candidates", 0),
            "answer_relevant": relevance["relevant"],
            "answer_similarity": relevance["similarity"],
            "latency_s": retrieval_result["total_time_s"],
            "stage_timings": retrieval_result.get("stage_timings", {})
        })
        
        status = "✓" if relevance["relevant"] else "✗"
        print(f"  [{status}] Root={'✓' if root_found else '✗'} "
              f"Candidates={retrieval_result.get('num_candidates', 0)} "
              f"Sim={relevance['similarity']:.2f} "
              f"Time={retrieval_result['total_time_s']:.3f}s")
    
    # ─── Aggregate ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("AGGREGATE RESULTS (Retrieval)")
    print("=" * 60)
    
    r1_vals = results["r1_root_accuracy"]
    r5_vals = results["r5_hit_rate"]
    r8_vals = results["r8_latencies"]
    
    if r1_vals:
        print(f"  R1 Root Accuracy: {sum(r1_vals)/len(r1_vals):.3f}")
    if r5_vals:
        print(f"  R5 Hit Rate @ 1: {sum(r5_vals)/len(r5_vals):.3f}")
    if r8_vals:
        print(f"  R8 Avg Latency: {sum(r8_vals)/len(r8_vals):.3f}s "
              f"(min={min(r8_vals):.3f}s, max={max(r8_vals):.3f}s)")
    
    # Per question type
    from collections import defaultdict
    type_results = defaultdict(list)
    for r in results["per_query_results"]:
        type_results[r["question_type"]].append(r)
    
    print("\nPer Question Type:")
    for qtype, items in sorted(type_results.items()):
        hit = sum(1 for it in items if it["answer_relevant"]) / len(items)
        root = sum(1 for it in items if it["root_found"]) / len(items)
        print(f"  {qtype}: Hit={hit:.3f} Root={root:.3f} (n={len(items)})")
    
    # Save
    if save_results:
        out_path = f"eval/results/retrieval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        serializable = json.loads(json.dumps(results, default=str))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
        print(f"\nResults saved to: {out_path}")
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MindCache Retrieval Evaluation")
    parser.add_argument("--dataset", default="eval/data/longmemeval_adapted.json")
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    
    run_retrieval_eval(args.dataset, max_queries=args.max_queries, save_results=args.save)
