"""
eval/eval_beam_e2e.py — End-to-End BEAM Evaluation for MindCache
=================================================================

PURPOSE
-------
This script is the **complete, self-contained evaluation pipeline** for
testing MindCache against a BEAM (Benchmark for Evaluating long-context AI
Memory) conversation dataset.

It runs three sequential stages in one command:

  Stage 1 │ adapt_beam   — Convert a BEAM parquet file → MindCache JSON format
  Stage 2 │ create_jobs  — Queue ingestion jobs in the local SQLite database
  Stage 3 │ eval_retrieval — Run retrieval over all test questions and save results

HOW TO USE (for external researchers / library users)
------------------------------------------------------
1. Install MindCache:
       pip install -e .          # from the repo root

2. Download a BEAM parquet file (e.g. BEAM-1M or BEAM-10M) from HuggingFace:
       https://huggingface.co/datasets/ServiceNow/BEAM

3. Run the full pipeline:
       python eval/eval_beam_e2e.py \\
           --parquet  BEAM/1M-00000-of-00001.parquet \\
           --conv-index 7 \\
           --db-path   mindcache.db \\
           --output-dir results/

   --parquet      Path to the BEAM .parquet file
   --conv-index   Which conversation to evaluate (0-based row index in the parquet)
   --db-path      Path where the MindCache SQLite database will be created
   --output-dir   Where to write the JSON results file
   --user-id      User ID used in the MindCache memory tree (default: "default")
   --skip-ingest  Skip Stage 1 & 2 and go straight to retrieval (if already ingested)
   --adapted-json Override the auto-generated adapted JSON path (use an existing file)
   --limit        Only evaluate the first N test questions (useful for quick smoke tests)

PREREQUISITES
-------------
- A RUNNING MindCache ingestion worker is NOT required for evaluation-only mode.
  The ingestion here is done synchronously (no separate process needed).
- You need a Gemini API key set in your environment:
       set GEMINI_API_KEY=your-key-here   (Windows)
       export GEMINI_API_KEY=your-key-here (Linux/macOS)

EXAMPLE — quick smoke test (first 5 questions, conversation #8):
       python eval/eval_beam_e2e.py --conv-index 7 --limit 5

OUTPUT
------
A JSON file in <output-dir>/ named after the conversation, e.g.:
    results/beam_conv7_results.json

Each entry contains:
    question_id, question_type, detected_type, question,
    expected, rubric, system_prompt, retrieved_context
"""

import json
import argparse
import sys
import os
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eval_beam_e2e")

# Allow running from the repo root or from the eval/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Adapt BEAM parquet → MindCache JSON
# ─────────────────────────────────────────────────────────────────────────────

def adapt_beam(parquet_path: str, output_path: str, conv_index: int = 7) -> dict:
    """
    Convert one BEAM conversation from parquet into the MindCache ingestion format.

    Picks the conversation at `conv_index` (0-based row index in the parquet).
    The default index 7 corresponds to the "Deep Dive into Number Theory"
    conversation in the BEAM-1M dataset.

    Transformations applied:
      - Strips '->>->' routing suffixes from user-turn content
      - Extracts per-session timestamp from the first turn's time_anchor field
      - Maps source_chat_ids (turn IDs) to session IDs for evidence provenance
      - Normalises heterogeneous answer field names across probing question categories

    Returns a dict in the format expected by create_jobs():
        {"sessions": [...], "test_cases": [...], "metadata": {...}}

    Also writes this dict to `output_path` as JSON.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas is required for adapt_beam. Install with: pip install pandas pyarrow")
        sys.exit(1)
    import ast

    log.info(f"[Stage 1] Loading parquet: {parquet_path}")
    df = pd.read_parquet(parquet_path)

    if conv_index >= len(df):
        log.error(f"conv_index {conv_index} is out of range — dataset has {len(df)} conversations.")
        sys.exit(1)

    r = df.iloc[conv_index]
    conv_id  = r['conversation_id']
    chat     = r['chat']
    seed     = r['conversation_seed']

    # ── Helper: flatten nested session turns from plan-dict structure ─────────
    def flatten_session_turns(sess):
        turns    = []
        seen_ids = set()
        if isinstance(sess, dict):
            for plan_name, plan_batches in sess.items():
                if plan_batches is None:
                    continue
                for batch in plan_batches:
                    if not isinstance(batch, dict):
                        continue
                    for turn_list in batch.get('turns', []):
                        if isinstance(turn_list, dict):
                            turn_list = [turn_list]
                        for t in turn_list:
                            if isinstance(t, dict) and 'id' in t:
                                if t['id'] not in seen_ids:
                                    seen_ids.add(t['id'])
                                    turns.append(t)
        return turns

    flat_sessions = [flatten_session_turns(sess) for sess in chat]

    log.info(
        f"[Stage 1] Conversation #{conv_id}: '{seed.get('title','?')}' "
        f"({seed.get('category','?')}) — "
        f"{len(chat)} sessions, {sum(len(s) for s in flat_sessions)} total turns"
    )

    # ── Build turn_id → session_index lookup ─────────────────────────────────
    turn_to_session: dict = {}
    for si, flat_turns in enumerate(flat_sessions):
        for t in flat_turns:
            turn_to_session[t['id']] = si

    # ── Convert sessions ──────────────────────────────────────────────────────
    sessions = []
    for si, flat_turns in enumerate(flat_sessions):
        timestamp = next(
            (t['time_anchor'] for t in flat_turns if t.get('time_anchor')), None
        )
        turns = []
        for t in flat_turns:
            content = t['content']
            # Strip the '->->' routing suffix (BEAM internal routing artifact)
            if '->->' in content:
                content = content.split('->->')[0].strip()
            turns.append({
                "role":    t['role'],
                "turn_id": t.get("id"),
                "content": content,
            })
        session_id = f"sess_{si}"
        sessions.append({"id": session_id, "timestamp": timestamp, "content": turns})
        log.info(f"  Session {si}: {len(turns)} turns | ts={timestamp} | id={session_id}")

    # ── Build test_cases from probing questions ───────────────────────────────
    pq = r['probing_questions']
    if isinstance(pq, str):
        pq = ast.literal_eval(pq)

    def flatten_ids(item):
        """Recursively extract all integer turn IDs from a nested structure."""
        if item is None:
            return []
        if isinstance(item, (int, str)):
            try:
                return [int(item)]
            except ValueError:
                return []
        if isinstance(item, list):
            out = []
            for x in item:
                out.extend(flatten_ids(x))
            return out
        if isinstance(item, dict):
            out = []
            for v in item.values():
                out.extend(flatten_ids(v))
            return out
        return []

    test_cases = []
    for cat, qs in pq.items():
        for i, q in enumerate(qs):
            ans_key = next(
                (k for k in ['answer', 'ideal_response', 'ideal_answer', 'ideal_summary']
                 if k in q and q[k]),
                None
            )
            answer  = str(q[ans_key]) if ans_key else ""
            raw_src = q.get('source_chat_ids', q.get('conversation_sessions'))

            evidence_session_ids: set = set()
            if raw_src and raw_src != 'N/A':
                for tid in flatten_ids(raw_src):
                    sid = turn_to_session.get(tid)
                    if sid is not None:
                        evidence_session_ids.add(f"sess_{sid}")

            test_cases.append({
                "question_id":  f"{cat}_{i}",
                "question_type": cat,
                "question":     q['question'],
                "answer":       answer,
                "answer_field": ans_key or "compliance",
                "difficulty":   q.get('difficulty', ''),
                "rubric":       q.get('rubric', []),
                "session_ids":  sorted(evidence_session_ids),
                "turn_ids":     raw_src,   # kept for debugging
            })

    output_data = {
        "metadata": {
            "source":          "BEAM",
            "conversation_id": str(conv_id),
            "title":           seed.get('title', ''),
            "category":        seed.get('category', ''),
            "parquet_index":   conv_index,
            "total_sessions":  len(sessions),
            "total_turns":     sum(len(s['content']) for s in sessions),
            "total_questions": len(test_cases),
        },
        "sessions":   sessions,
        "test_cases": test_cases,
    }

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    log.info(
        f"[Stage 1] Saved adapted dataset → {output_path}  "
        f"({len(sessions)} sessions, {len(test_cases)} test cases)"
    )
    return output_data


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Ingest adapted JSON into MindCache (create & run jobs)
# ─────────────────────────────────────────────────────────────────────────────

def ingest_adapted(adapted_json_path: str, db_path: str):
    """
    Load the adapted BEAM JSON and ingest all sessions into MindCache.

    Internally this:
      1. Resets the DB (drop + re-create tables)
      2. Batches sessions into ProcessingJob rows  (create_jobs)
      3. Runs the Memory Extractor on each job     (run_jobs)

    NOTE: This performs real LLM API calls (Gemini) to extract memories.
    Make sure GEMINI_API_KEY is set in your environment before running.
    """
    # Import here so the script can still run --help without the full stack
    from ingest_api import create_jobs, run_jobs

    log.info(f"[Stage 2] Creating ingestion jobs from: {adapted_json_path}")
    create_jobs(adapted_json_path, reset=True)

    log.info("[Stage 2] Running extraction jobs (this may take several minutes)…")
    run_jobs(limit=9999)   # process all jobs

    log.info("[Stage 2] Ingestion complete.")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Retrieval evaluation
# ─────────────────────────────────────────────────────────────────────────────

def run_retrieval_eval(
    test_cases: list,
    db_path: str,
    output_dir: str,
    conv_id: str,
    user_id: str = "default",
    limit: int = None,
) -> str:
    """
    Run MindCache retrieval over every test question and record the results.

    For each question the retriever is called with use_reranker=False
    (the no-reranker configuration that performed best in our evaluations).

    Results are saved to:
        <output_dir>/beam_conv<conv_id>_results.json

    Returns the path to the saved JSON file.
    """
    from mindcache import MindCache
    from mindcache.retrieval.root_cache import refresh_tree_cache

    os.makedirs(output_dir, exist_ok=True)

    if limit:
        test_cases = test_cases[:limit]

    log.info(f"[Stage 3] Initialising MindCache (db={db_path}, user={user_id})")
    mc = MindCache(db_path=db_path)

    # Always refresh the tree cache when switching databases so no stale
    # in-memory or on-disk cache from a previous run pollutes results.
    refresh_tree_cache(user_id=user_id)

    results = []
    for i, tc in enumerate(test_cases):
        q_id     = tc.get("question_id",   f"q_{i}")
        q_type   = tc.get("question_type", "unknown")
        q_text   = tc.get("question",      "")
        expected = tc.get("answer", tc.get("expected", ""))
        rubric   = tc.get("rubric",        [])

        log.info(f"[Stage 3] [{i+1}/{len(test_cases)}] {q_text[:80]}…")

        t0 = time.time()
        retrieval_res     = mc.retriever.retrieve(q_text, user_id=user_id, use_reranker=False)
        retrieval_latency = time.time() - t0

        retrieval_context = retrieval_res.context
        system_prompt     = retrieval_res.system_hint
        detected_type     = retrieval_res.query_type or q_type

        log.info(
            f"  → context={len(retrieval_context)} chars | "
            f"type={detected_type} | latency={retrieval_latency:.2f}s"
        )

        results.append({
            "question_id":       q_id,
            "question_type":     q_type,
            "detected_type":     detected_type,
            "question":          q_text,
            "expected":          expected,
            "rubric":            rubric,
            "system_prompt":     system_prompt,
            "retrieved_context": retrieval_context,
            "retrieval_latency": round(retrieval_latency, 3),
        })
        time.sleep(0.5)   # be gentle on any background threads

    # Save results
    out_name = f"beam_conv{conv_id}_results.json"
    out_path = os.path.join(output_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info(f"[Stage 3] Results saved → {out_path}  ({len(results)} questions)")
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end BEAM evaluation for MindCache.\n"
            "Runs: adapt → ingest → retrieval eval in one command.\n\n"
            "See the module docstring at the top of this file for full usage docs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--parquet",
        default="BEAM/1M-00000-of-00001.parquet",
        help="Path to the BEAM .parquet file (default: BEAM/1M-00000-of-00001.parquet)",
    )
    parser.add_argument(
        "--conv-index",
        type=int,
        default=7,
        help="0-based row index of the conversation to evaluate (default: 7)",
    )
    parser.add_argument(
        "--db-path",
        default="mindcache.db",
        help="Path to the MindCache SQLite database (will be created/reset)",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="Directory where the results JSON will be written (default: results/)",
    )
    parser.add_argument(
        "--user-id",
        default="default",
        help="User ID for the MindCache memory tree (default: 'default')",
    )
    parser.add_argument(
        "--adapted-json",
        default=None,
        help=(
            "Path for the intermediate adapted JSON file.  "
            "Defaults to <output-dir>/beam_adapted_conv<N>.json"
        ),
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help=(
            "Skip Stages 1 & 2 (adaptation + ingestion) and jump straight to retrieval eval.  "
            "Requires --adapted-json pointing to an already-adapted file and a pre-populated DB."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only evaluate the first N test questions (handy for quick smoke tests)",
    )

    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.adapted_json is None:
        args.adapted_json = os.path.join(
            args.output_dir, f"beam_adapted_conv{args.conv_index}.json"
        )

    # ── Stage 1 & 2: Adapt + Ingest ──────────────────────────────────────────
    if not args.skip_ingest:
        adapted_data = adapt_beam(args.parquet, args.adapted_json, args.conv_index)
        ingest_adapted(args.adapted_json, args.db_path)
    else:
        log.info(f"[Stage 1-2] Skipped — loading adapted JSON from: {args.adapted_json}")
        with open(args.adapted_json, "r", encoding="utf-8") as f:
            adapted_data = json.load(f)

    # ── Stage 3: Retrieval Evaluation ────────────────────────────────────────
    conv_id    = adapted_data.get("metadata", {}).get("conversation_id", str(args.conv_index))
    test_cases = adapted_data.get("test_cases", [])

    out_path = run_retrieval_eval(
        test_cases  = test_cases,
        db_path     = args.db_path,
        output_dir  = args.output_dir,
        conv_id     = conv_id,
        user_id     = args.user_id,
        limit       = args.limit,
    )

    log.info("=" * 60)
    log.info("Evaluation complete.")
    log.info(f"Results: {out_path}")
    log.info("=" * 60)
