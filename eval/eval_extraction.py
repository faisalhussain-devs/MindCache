"""
MindCache Evaluation — Extraction Pipeline (E1-E6)

Evaluates the Memory_Extractor's ability to correctly extract structured
memories from raw chat triads.

Usage:
    python eval/eval_extraction.py --dataset eval/data/golden_dataset.json
    python eval/eval_extraction.py --dataset eval/data/golden_dataset.json --save
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Memory_extract.schema import ChatExtraction
from Memory_extract.memory_extractor import Memory_Extractor
from eval.metrics import (
    fuzzy_match_memories,
    category_metrics,
    cross_category_leakage,
    topic_jaccard,
    aggregate_results
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
    """Embed a list of strings → np.ndarray."""
    import numpy as np
    model = get_embedder()
    if not texts:
        return np.array([])
    return model.encode(texts, convert_to_numpy=True)


# ─── E1: Schema Compliance ───────────────────────────────────────
def eval_schema_compliance(raw_output: str) -> Dict:
    """Check if the raw output is valid ChatExtraction JSON."""
    try:
        validated = ChatExtraction.model_validate_json(raw_output)
        return {"valid": True, "error": None}
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ─── E2/E3: Category Precision & Recall ──────────────────────────
def extract_category_items(extraction_data: Dict) -> Dict[str, List[str]]:
    """
    Flatten a ChatExtraction output into per-category item lists.
    Handles both golden dataset format and model output format.
    """
    result = {"user": [], "fact": [], "epis": [], "decision": []}
    
    memories = extraction_data.get("memory", [])
    if isinstance(memories, list):
        for bucket in memories:
            if isinstance(bucket, dict):
                for cat in result.keys():
                    items = bucket.get(cat, [])
                    if isinstance(items, list):
                        result[cat].extend(items)
    elif isinstance(memories, dict):
        # Alternative format: memory is a dict directly
        for cat in result.keys():
            items = memories.get(cat, [])
            if isinstance(items, list):
                result[cat].extend(items)
    
    return result


# ─── E4: Topic Accuracy ──────────────────────────────────────────
def extract_topics(extraction_data: Dict) -> List[str]:
    """Extract all topic names from extraction output."""
    topics = []
    
    # Root topics
    root = extraction_data.get("topics_root", [])
    if isinstance(root, list):
        topics.extend(root)
    
    # Branch topics from memory buckets
    memories = extraction_data.get("memory", [])
    if isinstance(memories, list):
        for bucket in memories:
            if isinstance(bucket, dict):
                branch = bucket.get("topics_branch", [])
                if isinstance(branch, list):
                    topics.extend(branch)
    elif isinstance(memories, dict):
        topics.extend(memories.get("topics", []))
    
    return topics


# ─── E6: Hallucination Rate ──────────────────────────────────────
def check_hallucination_simple(item: str, source_text: str, threshold: float = 0.6) -> bool:
    """
    Simple grounding check: is the extracted item grounded in the source text?
    Uses embedding similarity between the item and the source.
    Items with very low similarity to any part of the source are hallucinations.
    
    Note: For more robust checking, use LLM-as-judge instead.
    """
    import numpy as np
    
    # Split source into chunks for comparison
    sentences = [s.strip() for s in source_text.replace("\n", ". ").split(". ") if len(s.strip()) > 10]
    
    if not sentences:
        return True  # Can't verify, assume hallucinated
    
    item_vec = embed_fn([item])
    source_vecs = embed_fn(sentences)
    
    from eval.metrics import batch_cosine_similarity
    sims = batch_cosine_similarity(item_vec[0], source_vecs)
    max_sim = float(np.max(sims))
    
    return max_sim < threshold


# ─── Main Evaluation ─────────────────────────────────────────────
def run_extraction_eval(dataset_path: str, save_results: bool = False) -> Dict:
    """
    Run E1-E6 metrics on a golden dataset.
    
    Expected dataset format:
    [
        {
            "input": "<user>...<ChatGPT>...",
            "output": { "reasoning": [...], "memory": [...], ... }
        }
    ]
    """
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    
    print(f"[Eval] Loaded {len(dataset)} examples from {dataset_path}")
    
    extractor = Memory_Extractor()
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "dataset": dataset_path,
        "num_examples": len(dataset),
        "e1_schema_compliance": [],
        "e2_e3_category_metrics": [],
        "e4_topic_accuracy": [],
        "e5_cross_category_leakage": [],
        "e6_hallucination": [],
        "latency_per_example_seconds": [],
    }
    
    for i, example in enumerate(dataset):
        print(f"\n--- Example {i+1}/{len(dataset)} ---")
        
        raw_input = example["input"]
        golden_output = example["output"]
        
        # Run extraction
        start_time = time.perf_counter()
        predicted = extractor.memory_extract(raw_input)
        elapsed = time.perf_counter() - start_time
        results["latency_per_example_seconds"].append(elapsed)
        
        if predicted is None:
            print(f"  [FAIL] Extraction returned None")
            results["e1_schema_compliance"].append({"valid": False, "error": "returned None"})
            continue
        
        # E1: Schema Compliance (already validated by memory_extract, but track it)
        results["e1_schema_compliance"].append({"valid": True, "error": None})
        
        # E2/E3: Category Precision & Recall
        pred_items = extract_category_items(predicted)
        gold_items = extract_category_items(golden_output)
        cat_result = category_metrics(pred_items, gold_items, embed_fn, threshold=0.75)
        results["e2_e3_category_metrics"].append(cat_result)
        
        for cat, scores in cat_result.items():
            print(f"  [{cat}] P={scores['precision']:.2f} R={scores['recall']:.2f} F1={scores['f1']:.2f}")
        
        # E4: Topic Accuracy
        pred_topics = extract_topics(predicted)
        gold_topics = extract_topics(golden_output)
        jaccard = topic_jaccard(pred_topics, gold_topics)
        results["e4_topic_accuracy"].append({"jaccard": jaccard, "predicted": pred_topics, "golden": gold_topics})
        print(f"  [Topics] Jaccard={jaccard:.2f} Pred={pred_topics} Gold={gold_topics}")
        
        # E5: Cross-Category Leakage
        leakage = cross_category_leakage(pred_items, gold_items, embed_fn, threshold=0.75)
        results["e5_cross_category_leakage"].append(leakage)
        print(f"  [Leakage] Rate={leakage['leakage_rate']:.2f} ({len(leakage['leaked_items'])} items)")
        
        # E6: Hallucination (simple embedding check)
        all_pred_items = []
        for cat_items in pred_items.values():
            all_pred_items.extend(cat_items)
        
        hallucinated = sum(1 for item in all_pred_items if check_hallucination_simple(item, raw_input))
        total_items = len(all_pred_items)
        hall_rate = hallucinated / total_items if total_items > 0 else 0.0
        results["e6_hallucination"].append({
            "rate": hall_rate, "hallucinated": hallucinated, "total": total_items
        })
        print(f"  [Hallucination] Rate={hall_rate:.2f} ({hallucinated}/{total_items})")
        print(f"  [Latency] {elapsed:.2f}s")
    
    # ─── Aggregate ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("AGGREGATE RESULTS")
    print("=" * 60)
    
    # E1 aggregate
    valid_count = sum(1 for r in results["e1_schema_compliance"] if r["valid"])
    print(f"E1 Schema Compliance: {valid_count}/{len(results['e1_schema_compliance'])}")
    
    # E2/E3 aggregate
    if results["e2_e3_category_metrics"]:
        for cat in ["user", "fact", "epis", "decision"]:
            precisions = [r[cat]["precision"] for r in results["e2_e3_category_metrics"] if cat in r]
            recalls = [r[cat]["recall"] for r in results["e2_e3_category_metrics"] if cat in r]
            f1s = [r[cat]["f1"] for r in results["e2_e3_category_metrics"] if cat in r]
            if precisions:
                print(f"E2/E3 [{cat}] P={sum(precisions)/len(precisions):.3f} "
                      f"R={sum(recalls)/len(recalls):.3f} F1={sum(f1s)/len(f1s):.3f}")
    
    # E4 aggregate
    jaccards = [r["jaccard"] for r in results["e4_topic_accuracy"]]
    if jaccards:
        print(f"E4 Topic Accuracy (Jaccard): {sum(jaccards)/len(jaccards):.3f}")
    
    # E5 aggregate
    leakage_rates = [r["leakage_rate"] for r in results["e5_cross_category_leakage"]]
    if leakage_rates:
        print(f"E5 Cross-Category Leakage: {sum(leakage_rates)/len(leakage_rates):.3f}")
    
    # E6 aggregate
    hall_rates = [r["rate"] for r in results["e6_hallucination"]]
    if hall_rates:
        print(f"E6 Hallucination Rate: {sum(hall_rates)/len(hall_rates):.3f}")
    
    # Latency
    latencies = results["latency_per_example_seconds"]
    if latencies:
        print(f"\nLatency: avg={sum(latencies)/len(latencies):.2f}s "
              f"min={min(latencies):.2f}s max={max(latencies):.2f}s")
    
    # Save
    if save_results:
        out_path = f"eval/results/extraction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # Convert non-serializable items
        serializable = json.loads(json.dumps(results, default=str))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
        print(f"\nResults saved to: {out_path}")
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MindCache Extraction Evaluation")
    parser.add_argument("--dataset", default="data/golden_dataset.json",
                        help="Path to golden dataset JSON")
    parser.add_argument("--save", action="store_true", help="Save results to file")
    args = parser.parse_args()
    
    run_extraction_eval(args.dataset, save_results=args.save)
