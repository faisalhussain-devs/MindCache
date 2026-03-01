"""
MindCache Evaluation — LongMemEval Optimization Adapter (Merge Strategy)

Optimized for LongMemEval-M (1.2M tokens/haystack).
Strategy:
1. Select ONE representative "base" haystack (e.g., from 'answer_ultrachat' group).
2. For N other questions, extract ONLY their evidence sessions.
3. Inject those evidence sessions into the base haystack's session list.
4. Generate a single "super-haystack" or a set of questions that all run against
   mostly the same DB state.

This dramatically reduces API costs by avoiding 500x ingestions of 1.2M tokens.
Questions share ~99.5% of the haystack (distractors), differing only by <1% (evidence).

Output Format:
[
  {
    "base_haystack_id": "...", 
    "base_sessions": [...],  # The full 1.2M token haystack
    "injected_evidence": [...], # All evidence sessions from other questions
    "test_cases": [
      {
        "question_id": "...",
        "question": "...",
        "answer": "...",
        "evidence_session_ids": [...] # IDs of sessions that must be present
      },
      ...
    ]
  }
]
"""

import json
import argparse
import random
from collections import defaultdict
import ijson  # Required for large M file

def create_merged_dataset(input_path: str, output_path: str, base_idx: int = 0, num_questions: int = 50):
    """
    Create a merged dataset using the M variant.
    """
    print(f"Loading M dataset structure from {input_path}...")
    
    base_item = None
    candidate_questions = []
    collected_evidence = {}
    
    with open(input_path, "rb") as f:
        parser = ijson.items(f, "item")
        for i, item in enumerate(parser):
            if i == base_idx:
                base_item = item
                print(f"Selected Base Haystack #{i}: ID={item['question_id']}")
                print(f"  Sessions: {len(item['haystack_sessions'])}")
            elif len(candidate_questions) < num_questions:
                evd_ids = set(item.get("answer_session_ids", []))
                candidate_questions.append({
                    "question_id": item["question_id"],
                    "question_type": item["question_type"],
                    "question": item["question"],
                    "answer": item["answer"],
                    "evidence_ids": evd_ids
                })
                sess_map = {sid: idx for idx, sid in enumerate(item["haystack_session_ids"])}
                dates = item.get("haystack_dates", [])
                for sid in evd_ids:
                    idx = sess_map[sid]
                    collected_evidence[sid] = {
                        "content": item["haystack_sessions"][idx],
                        "timestamp": dates[idx] if idx < len(dates) else None
                    }

            if base_item is not None and len(candidate_questions) >= num_questions:
                break
    
    if not base_item:
        raise ValueError(f"Base index {base_idx} out of range")
    
    print(f"Selected {len(candidate_questions)} questions to merge.")

    base_session_ids = set(base_item["haystack_session_ids"])
    base_dates = base_item.get("haystack_dates", [])
    final_sessions = []
    
    for idx, (sid, content) in enumerate(zip(base_item["haystack_session_ids"], base_item["haystack_sessions"])):
        ts = base_dates[idx] if idx < len(base_dates) else None
        final_sessions.append({"id": sid, "content": content, "timestamp": ts})
    
    print(f"Base has {len(final_sessions)} sessions.")
    
    injected_count = 0
    for sid, evd in collected_evidence.items():
        if sid not in base_session_ids:
            pos = random.randint(0, len(final_sessions))
            final_sessions.insert(pos, {"id": sid, "content": evd["content"], "timestamp": evd["timestamp"]})
            injected_count += 1
    
    print(f"Injected {injected_count} new evidence sessions.")
    print(f"Total merged sessions: {len(final_sessions)}")
    
    candidate_questions.append({
        "question_id": base_item["question_id"],
        "question_type": base_item["question_type"],
        "question": base_item["question"],
        "answer": str(base_item.get("answer", "")),
        "evidence_ids": set(base_item.get("answer_session_ids", []))
    })
    
    for cand in candidate_questions:
        cand["answer"] = str(cand["answer"])
        cand["evidence_ids"] = list(cand["evidence_ids"])
    
    output_data = {
        "metadata": {
            "source": input_path,
            "base_index": base_idx,
            "base_id": base_item["question_id"],
            "num_injected": injected_count,
            "total_questions": len(candidate_questions)
        },
        "sessions": final_sessions,
        "test_cases": candidate_questions
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    
    print(f"Saved merged dataset to {output_path}")
    print(f"Contains {len(final_sessions)} sessions and {len(candidate_questions)} test questions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="eval/data/longmemeval_m_cleaned.json")
    parser.add_argument("--output", default="eval/data/longmemeval_m_merged.json")
    parser.add_argument("--num", type=int, default=50)
    args = parser.parse_args()
    
    create_merged_dataset(args.input, args.output, num_questions=args.num)
