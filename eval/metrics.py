"""
MindCache Evaluation — Shared Metrics Module
Provides fuzzy matching, cosine similarity, and scoring helpers
used across all eval scripts.
"""
import numpy as np
from typing import List, Dict, Optional, Tuple
from collections import defaultdict


# Cosine Similarity
def batch_cosine_similarity(query_vec: np.ndarray, candidate_vecs: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a query and a batch of candidates."""
    if query_vec is None or candidate_vecs is None:
        return 0.0
    return float(np.dot(candidate_vecs, query_vec))


# ─── Fuzzy Matching (Embedding-Based) ────────────────────────────
def fuzzy_match_memories(
    predicted: List[str],
    golden: List[str],
    embedder_fn,
    thresholds: List[float] = [0.7, 0.75, 0.8]
) -> Dict[float, Dict]:
    """
    Match predicted memories to golden memories using cosine similarity.
    
    Args:
        predicted: List of predicted memory strings
        golden: List of golden (ground truth) memory strings
        embedder_fn: Function that takes a list of strings → np.ndarray of shape (N, D)
        thresholds: Similarity thresholds to evaluate at
    
    Returns:
        Dict mapping threshold → {precision, recall, f1, matches}
    """
    if not predicted or not golden:
        return {t: {"precision": 0.0, "recall": 0.0, "f1": 0.0, "matches": []} for t in thresholds}
    
    pred_vecs = embedder_fn(predicted)
    gold_vecs = embedder_fn(golden)
    
    # Build similarity matrix: (len(predicted), len(golden))
    sim_matrix = np.zeros((len(predicted), len(golden)))
    for i, pv in enumerate(pred_vecs):
        sim_matrix[i] = batch_cosine_similarity(pv, gold_vecs)
    
    results = {}
    for threshold in thresholds:
        matched_gold = set()
        matched_pred = set()
        matches = []
        
        # Greedy matching: highest similarity first
        flat_indices = np.argsort(sim_matrix.ravel())[::-1]
        for flat_idx in flat_indices:
            i, j = divmod(int(flat_idx), len(golden))
            if i in matched_pred or j in matched_gold:
                continue
            if sim_matrix[i, j] >= threshold:
                matched_pred.add(i)
                matched_gold.add(j)
                matches.append({
                    "predicted": predicted[i],
                    "golden": golden[j],
                    "similarity": float(sim_matrix[i, j])
                })
        
        tp = len(matches)
        precision = tp / len(predicted) if predicted else 0.0
        recall = tp / len(golden) if golden else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        results[threshold] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": len(predicted) - tp,
            "fn": len(golden) - tp,
            "matches": matches
        }
    
    return results


# ─── Category-Level Metrics ──────────────────────────────────────
def category_metrics(
    predicted: Dict[str, List[str]],
    golden: Dict[str, List[str]],
    embedder_fn,
    threshold: float = 0.75
) -> Dict[str, Dict]:
    """
    Compute per-category precision/recall/F1 for memory types.
    
    Args:
        predicted: {"user": [...], "fact": [...], "epis": [...], "decision": [...]}
        golden: same format
        embedder_fn: embedding function
        threshold: similarity threshold
    
    Returns:
        Dict[category_name → {precision, recall, f1, tp, fp, fn}]
    """
    categories = ["user", "fact", "epis", "decision"]
    results = {}
    
    for cat in categories:
        pred_items = predicted.get(cat, [])
        gold_items = golden.get(cat, [])
        
        if not pred_items and not gold_items:
            results[cat] = {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
            continue
        
        match_result = fuzzy_match_memories(pred_items, gold_items, embedder_fn, [threshold])
        results[cat] = match_result[threshold]
    
    return results


# ─── Cross-Category Leakage (E5) ─────────────────────────────────
def cross_category_leakage(
    predicted: Dict[str, List[str]],
    golden: Dict[str, List[str]],
    embedder_fn,
    threshold: float = 0.75
) -> Dict:
    """
    Detect items placed in the wrong category.
    For each predicted item in category X, check if it matches better
    with a golden item in a different category Y.
    
    Returns:
        {"leakage_rate": float, "leaked_items": [...]}
    """
    categories = ["user", "fact", "epis", "decision"]
    
    # Flatten all golden items with their category labels
    all_golden = []
    golden_cats = []
    for cat in categories:
        for item in golden.get(cat, []):
            all_golden.append(item)
            golden_cats.append(cat)
    
    if not all_golden:
        return {"leakage_rate": 0.0, "leaked_items": [], "total_checked": 0}
    
    gold_vecs = embedder_fn(all_golden)
    leaked = []
    total_checked = 0
    
    for pred_cat in categories:
        pred_items = predicted.get(pred_cat, [])
        if not pred_items:
            continue
        
        pred_vecs = embedder_fn(pred_items)
        
        for i, pv in enumerate(pred_vecs):
            sims = batch_cosine_similarity(pv, gold_vecs)
            best_idx = int(np.argmax(sims))
            best_sim = sims[best_idx]
            
            if best_sim >= threshold:
                total_checked += 1
                actual_cat = golden_cats[best_idx]
                if actual_cat != pred_cat:
                    leaked.append({
                        "predicted_item": pred_items[i],
                        "predicted_category": pred_cat,
                        "actual_category": actual_cat,
                        "matched_golden": all_golden[best_idx],
                        "similarity": float(best_sim)
                    })
    
    leakage_rate = len(leaked) / total_checked if total_checked > 0 else 0.0
    return {
        "leakage_rate": leakage_rate,
        "leaked_items": leaked,
        "total_checked": total_checked
    }


# ─── Topic Accuracy (E4) ─────────────────────────────────────────
def topic_jaccard(predicted_topics: List[str], golden_topics: List[str]) -> float:
    """Jaccard similarity between two topic lists (case-insensitive)."""
    pred_set = {t.lower().strip() for t in predicted_topics}
    gold_set = {t.lower().strip() for t in golden_topics}
    if not pred_set and not gold_set:
        return 1.0
    if not pred_set or not gold_set:
        return 0.0
    intersection = pred_set & gold_set
    union = pred_set | gold_set
    return len(intersection) / len(union)


def topic_chain_overlap(predicted_chain: List[str], golden_chain: List[str]) -> float:
    """
    Ordered overlap between topic chains.
    Measures how many levels of the chain match in order.
    """
    if not predicted_chain or not golden_chain:
        return 0.0
    
    matches = 0
    for p, g in zip(predicted_chain, golden_chain):
        if p.lower().strip() == g.lower().strip():
            matches += 1
        else:
            break
    return matches / max(len(predicted_chain), len(golden_chain))


# ─── Retrieval Metrics ────────────────────────────────────────────
def hit_rate_at_k(retrieved_ids: List[int], target_id: int, k: int = 1) -> float:
    """1 if target_id appears in top-k retrieved_ids, else 0."""
    return 1.0 if target_id in retrieved_ids[:k] else 0.0


def reciprocal_rank(retrieved_ids: List[int], target_id: int) -> float:
    """1/rank of first occurrence of target_id in retrieved_ids."""
    for i, rid in enumerate(retrieved_ids):
        if rid == target_id:
            return 1.0 / (i + 1)
    return 0.0


def mean_reciprocal_rank(all_retrieved: List[List[int]], all_targets: List[int]) -> float:
    """MRR across all queries."""
    if not all_retrieved:
        return 0.0
    rrs = [reciprocal_rank(ret, tgt) for ret, tgt in zip(all_retrieved, all_targets)]
    return sum(rrs) / len(rrs)


# ─── RAPTOR Summarizer Metrics (S-Metrics) ────────────────────────
def source_map_id_validity(source_map: Dict, child_ids: List[int]) -> Dict:
    """
    S1: Check if all IDs in source_map are real children.
    Returns validity rate and list of phantom (hallucinated) IDs.
    """
    child_id_set = set(child_ids)
    map_ids = set()
    for k in source_map.keys():
        try:
            map_ids.add(int(k))
        except ValueError:
            map_ids.add(k)  # Keep as-is if not a number
    
    valid = map_ids & child_id_set
    phantom = map_ids - child_id_set
    
    total = len(map_ids)
    return {
        "validity_rate": len(valid) / total if total > 0 else 1.0,
        "valid_count": len(valid),
        "phantom_count": len(phantom),
        "phantom_ids": list(phantom),
        "total_in_map": total
    }


def ignored_id_validity(ignored_ids: List, child_ids: List[int]) -> Dict:
    """
    S2: Check if all ignored IDs are real children.
    """
    child_id_set = set(child_ids)
    ignored_set = set()
    for k in ignored_ids:
        try:
            ignored_set.add(int(k))
        except (ValueError, TypeError):
            ignored_set.add(k)
    
    valid = ignored_set & child_id_set
    phantom = ignored_set - child_id_set
    
    total = len(ignored_set)
    return {
        "validity_rate": len(valid) / total if total > 0 else 1.0,
        "valid_count": len(valid),
        "phantom_count": len(phantom),
        "phantom_ids": list(phantom),
        "total_ignored": total
    }


def coverage_rate(source_map: Dict, ignored_ids: List, child_ids: List[int]) -> Dict:
    """
    S3: % of children accounted for (in source_map OR ignored).
    """
    child_id_set = set(child_ids)
    accounted = set()
    
    for k in source_map.keys():
        try:
            accounted.add(int(k))
        except ValueError:
            pass
    for k in ignored_ids:
        try:
            accounted.add(int(k))
        except (ValueError, TypeError):
            pass
    
    covered = accounted & child_id_set
    uncovered = child_id_set - accounted
    
    total = len(child_id_set)
    return {
        "coverage_rate": len(covered) / total if total > 0 else 1.0,
        "covered_count": len(covered),
        "uncovered_count": len(uncovered),
        "uncovered_ids": list(uncovered),
        "total_children": total
    }


# ─── Aggregate Helpers ────────────────────────────────────────────
def aggregate_results(results_list: List[Dict]) -> Dict:
    """Average numeric values across a list of result dicts."""
    if not results_list:
        return {}
    
    aggregated = {}
    keys = results_list[0].keys()
    
    for key in keys:
        values = [r[key] for r in results_list if isinstance(r.get(key), (int, float))]
        if values:
            aggregated[key] = {
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "count": len(values)
            }
    
    return aggregated
