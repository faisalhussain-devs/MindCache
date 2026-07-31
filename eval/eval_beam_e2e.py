"""
eval/eval_beam_e2e.py — End-to-End BEAM Evaluation for MindCache
=================================================================

PURPOSE
-------
This script is the complete evaluation pipeline for testing MindCache against a
BEAM (Benchmark for Evaluating long-context AI Memory) conversation dataset,
leveraging the public MindCache library SDK.

It runs three sequential stages in one command:

  Stage 1 │ adapt_beam      — Convert a BEAM parquet → MindCache JSON format
  Stage 2 │ ingest_adapted  — Queue + run memory extraction using MindCache client
  Stage 3 │ eval_retrieval  — Run retrieval over all test questions, save results
"""

import json
import argparse
import sys
import os
import time
import logging
from datetime import datetime
from mindcache import MindCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eval_beam_e2e")

# Allow running from repo root or from eval/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stage 1 — Adapt BEAM parquet → MindCache JSON

def adapt_beam(parquet_path: str, output_path: str, conv_index: int = 7) -> dict:
    """
    Convert one BEAM conversation from parquet into MindCache ingestion format.

    Picks the conversation at `conv_index` (0-based row index).
    Default index 7 = "Deep Dive into Number Theory" in BEAM-1M.

    Returns the adapted dict and writes it to `output_path`.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas is required. Install with: pip install pandas pyarrow")
        sys.exit(1)
    import ast

    log.info(f"[Stage 1] Loading parquet: {parquet_path}")
    df = pd.read_parquet(parquet_path)

    if conv_index >= len(df):
        log.error(f"conv_index {conv_index} out of range — dataset has {len(df)} rows.")
        sys.exit(1)

    r       = df.iloc[conv_index]
    conv_id = r['conversation_id']
    chat    = r['chat']
    seed    = r['conversation_seed']

    def _flatten_session(sess):
        turns, seen = [], set()

        def _extract_turn(t):
            if isinstance(t, dict) and 'id' in t:
                if t['id'] not in seen:
                    seen.add(t['id'])
                    turns.append(t)
            elif hasattr(t, '__iter__') and not isinstance(t, (str, bytes, dict)):
                for item in t:
                    _extract_turn(item)

        if isinstance(sess, dict):
            for _, batches in sess.items():
                if not batches:
                    continue
                for batch in batches:
                    if isinstance(batch, dict):
                        for tl in batch.get('turns', []):
                            _extract_turn(tl)
                    elif hasattr(batch, '__iter__') and not isinstance(batch, (str, bytes, dict)):
                        for t in batch:
                            _extract_turn(t)
        elif hasattr(sess, '__iter__') and not isinstance(sess, (str, bytes, dict)):
            for item in sess:
                if isinstance(item, dict) and 'turns' in item:
                    for t in item.get('turns', []):
                        _extract_turn(t)
                else:
                    _extract_turn(item)
        return turns

    flat = [_flatten_session(s) for s in chat]
    title = seed.get('title', '?') if isinstance(seed, dict) else '?'
    category = seed.get('category', '?') if isinstance(seed, dict) else '?'
    log.info(
        f"[Stage 1] Conv #{conv_id}: '{title}' "
        f"({category}) — "
        f"{len(chat)} sessions, {sum(len(s) for s in flat)} turns"
    )

    turn_to_session = {t['id']: si for si, turns in enumerate(flat) for t in turns}

    sessions = []
    for si, turns in enumerate(flat):
        ts = next((t['time_anchor'] for t in turns if isinstance(t, dict) and t.get('time_anchor')), None)
        clean_turns = []
        for t in turns:
            content = t['content']
            if '->->' in content:
                content = content.split('->->')[0].strip()
            clean_turns.append({"role": t['role'], "turn_id": t.get("id"), "content": content})
        sessions.append({"id": f"sess_{si}", "timestamp": ts, "content": clean_turns})
        log.info(f"  Session {si}: {len(clean_turns)} turns | ts={ts}")

    pq = r['probing_questions']
    if isinstance(pq, str):
        pq = ast.literal_eval(pq)

    def _flatten_ids(item):
        if item is None: return []
        if isinstance(item, (int, str)):
            try: return [int(item)]
            except ValueError: return []
        if isinstance(item, list):
            out = []
            for x in item: out.extend(_flatten_ids(x))
            return out
        if isinstance(item, dict):
            out = []
            for v in item.values(): out.extend(_flatten_ids(v))
            return out
        return []

    test_cases = []
    for cat, qs in pq.items():
        for i, q in enumerate(qs):
            ans_key = next(
                (k for k in ['answer', 'ideal_response', 'ideal_answer', 'ideal_summary']
                 if k in q and q[k]), None
            )
            raw_src = q.get('source_chat_ids', q.get('conversation_sessions'))
            ev_sess = set()
            if raw_src and raw_src != 'N/A':
                for tid in _flatten_ids(raw_src):
                    sid = turn_to_session.get(tid)
                    if sid is not None:
                        ev_sess.add(f"sess_{sid}")
            test_cases.append({
                "question_id":   f"{cat}_{i}",
                "question_type": cat,
                "question":      q['question'],
                "answer":        str(q[ans_key]) if ans_key else "",
                "answer_field":  ans_key or "compliance",
                "difficulty":    q.get('difficulty', ''),
                "rubric":        q.get('rubric', []),
                "session_ids":   sorted(ev_sess),
                "turn_ids":      raw_src,
            })

    output_data = {
        "metadata": {
            "source":          "BEAM",
            "conversation_id": str(conv_id),
            "title":           seed.get('title', '') if isinstance(seed, dict) else '',
            "category":        seed.get('category', '') if isinstance(seed, dict) else '',
            "parquet_index":   conv_index,
            "total_sessions":  len(sessions),
            "total_turns":     sum(len(s['content']) for s in sessions),
            "total_questions": len(test_cases),
        },
        "sessions":   sessions,
        "test_cases": test_cases,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    log.info(
        f"[Stage 1] Saved → {output_path}  "
        f"({len(sessions)} sessions, {len(test_cases)} test cases)"
    )
    return output_data


# Stage 2 — Ingest adapted JSON using the MindCache Library

def ingest_adapted(
    adapted_data: dict, 
    db_path: str, 
    user_id: str = "default",
):
    """
    Ingest the adapted dataset sessions using the MindCache SDK client.
    Queues all turns under session groups and processes them dynamically.
    """
    # 1. Initialize client in exhaustive mode for BEAM evaluation
    mc = MindCache(db_path=db_path, enable_summarization=True)
    
    # 2. Reset the user database for a clean slate
    mc.reset(user_id=user_id)
    
    # 3. Add all sessions via the client API
    sessions = adapted_data.get("sessions", [])
    log.info(f"[Stage 2] Queuing {len(sessions)} sessions for user '{user_id}'")
    
    for session in sessions:
        ts = session.get("timestamp")
        timestamp_dt = None
        if ts:
            for fmt in ("%B-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    timestamp_dt = datetime.strptime(ts, fmt)
                    break
                except Exception:
                    continue
            if not timestamp_dt:
                try:
                    timestamp_dt = datetime.fromisoformat(ts)
                except Exception:
                    timestamp_dt = None
        messages = []
        for turn in session.get("content"):
            # Queue the turns in this session
            messages.append(turn)
            if turn.get("role") == "assistant":
                mc.add(
                    messages=messages,
                    user_id=user_id,
                    timestamp=timestamp_dt
                )
                messages = []
    while True:    
        log.info("[Stage 2] Processing queue (running automatic consolidation, batch embedding, and extraction)...")
        # 4. Process all queued jobs (setting high limit to process the entire queue)
        out = mc.process_queue(user_id=user_id, limit=30, consolidation_max_tokens=4000)
        log.info("[Stage 2] Ingestion complete.")
        if out == 0 or out == {"success": 0, "failed": 0}:
            break


# Stage 3 — Retrieval evaluation

def run_retrieval_eval(
    test_cases: list,
    db_path:    str,
    output_dir: str,
    conv_id:    str,
    user_id:    str = "default",
    limit:      int = None,
) -> str:
    """
    Run MindCache retrieval over every test question and record results.

    Saves to: <output_dir>/beam_conv<conv_id>_results.json
    Returns the path of the saved file.
    """
    os.makedirs(output_dir, exist_ok=True)
    if limit:
        test_cases = test_cases[:limit]

    log.info(f"[Stage 3] Initialising MindCache (db={db_path}, user={user_id})")
    mc = MindCache(db_path=db_path, enable_summarization=True)

    results = []
    for i, tc in enumerate(test_cases):
        q_id     = tc.get("question_id",   f"q_{i}")
        q_type   = tc.get("question_type", "unknown")
        q_text   = tc.get("question",      "")
        expected = tc.get("answer", tc.get("expected", ""))
        rubric   = tc.get("rubric",        [])

        log.info(f"[Stage 3] [{i+1}/{len(test_cases)}] {q_text[:80]}…")

        t0  = time.time()
        # Retrieve context string using standard API
        result = mc.search(q_text, user_id=user_id)
        context = result.context
        query_type = result.query_type
        sys_prompt = result.system_hint
        lat = round(time.time() - t0, 3)

        log.info(
            f"  → context={len(context)} chars | latency={lat}s"
        )
        results.append({
            "question_id":       q_id,
            "question_type":     q_type,
            "detected_type":     query_type,
            "question":          q_text,
            "expected":          expected,
            "rubric":            rubric,
            "system_hint":       sys_prompt,
            "retrieved_context": context,
            "retrieval_latency": lat,
        })
        time.sleep(0.5)

    out_path = os.path.join(output_dir, f"beam_conv{conv_id}_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info(f"[Stage 3] Results saved → {out_path}  ({len(results)} questions)")
    return out_path


# CLI Entry Point

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="End-to-end BEAM evaluation for MindCache using library SDK.",
    )
    parser.add_argument("--parquet",       default="BEAM/1M-00000-of-00001.parquet")
    parser.add_argument("--conv-index",    type=int, default=34)
    parser.add_argument("--db-path",       default="mindcache.db")
    parser.add_argument("--output-dir",    default="eval_results")
    parser.add_argument("--user-id",       default="default")
    parser.add_argument("--adapted-json",  default=None)
    parser.add_argument("--skip-ingest",   action="store_true")
    parser.add_argument("--limit",         type=int, default=None)
    args = parser.parse_args()

    # Set env vars for token and base_url if supplied


    os.makedirs(args.output_dir, exist_ok=True)
    if args.adapted_json is None:
        args.adapted_json = os.path.join(
            args.output_dir, f"beam_adapted_conv{args.conv_index}.json"
        )

    if not args.skip_ingest:
        adapted_data = adapt_beam(args.parquet, args.adapted_json, args.conv_index)
        ingest_adapted(
            adapted_data = adapted_data, 
            db_path      = args.db_path, 
            user_id      = args.user_id,
        )
    else:
        log.info(f"[Stage 1-2] Skipped — loading: {args.adapted_json}")
        with open(args.adapted_json, "r", encoding="utf-8") as f:
            adapted_data = json.load(f)

    conv_id    = adapted_data.get("metadata", {}).get("conversation_id", str(args.conv_index))
    test_cases = adapted_data.get("test_cases", [])

    out_path = run_retrieval_eval(
        test_cases = test_cases,
        db_path    = args.db_path,
        output_dir = args.output_dir,
        conv_id    = conv_id,
        user_id    = args.user_id,
        limit      = args.limit,
    )

    log.info("=" * 60)
    log.info("Evaluation complete.")
    log.info(f"Results → {out_path}")
    log.info("=" * 60)
