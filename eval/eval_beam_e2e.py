"""
eval/eval_beam_e2e.py — End-to-End BEAM Evaluation for MindCache
=================================================================

PURPOSE
-------
This script is the complete, self-contained evaluation pipeline for testing
MindCache against a BEAM (Benchmark for Evaluating long-context AI Memory)
conversation dataset.

It runs three sequential stages in one command:

  Stage 1 │ adapt_beam      — Convert a BEAM parquet → MindCache JSON format
  Stage 2 │ ingest_adapted  — Queue + run memory extraction into the local DB
  Stage 3 │ eval_retrieval  — Run retrieval over all test questions, save results

HOW TO USE
----------
1. Install MindCache:
       pip install -e .

2. Download a BEAM parquet from HuggingFace:
       https://huggingface.co/datasets/ServiceNow/BEAM

3. Set your Gemini API key (needed for Stage 2 memory extraction):
       set GEMINI_API_KEY=your-key-here      (Windows)
       export GEMINI_API_KEY=your-key-here   (Linux/macOS)

4. Run the full pipeline:
       python eval/eval_beam_e2e.py \\
           --parquet    BEAM/1M-00000-of-00001.parquet \\
           --conv-index 7 \\
           --db-path    mindcache.db \\
           --output-dir results/

ARGUMENTS
---------
  --parquet       Path to the BEAM .parquet file
  --conv-index    0-based row index of the conversation to evaluate (default: 7)
  --db-path       Path where the MindCache SQLite DB will be created/reset
  --output-dir    Directory to write the result JSON (default: results/)
  --user-id       User ID for the memory tree (default: "default")
  --adapted-json  Override path for the intermediate adapted JSON file
  --skip-ingest   Skip Stages 1 & 2, jump straight to retrieval (DB already populated)
  --limit         Only evaluate the first N questions (quick smoke test)

QUICK SMOKE TEST (5 questions, no full ingest needed with --skip-ingest):
       python eval/eval_beam_e2e.py --skip-ingest --adapted-json my_adapted.json --limit 5

OUTPUT
------
  results/beam_conv<ID>_results.json
  One entry per question: question_id, question_type, detected_type, question,
  expected, rubric, system_prompt, retrieved_context, retrieval_latency
"""

import json
import argparse
import sys
import os
import re as _re
import time
import logging
import subprocess
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eval_beam_e2e")

# Allow running from repo root or from eval/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion constants & helpers  (self-contained, no ingest_api dependency)
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_BUDGET   = 4_000   # max tokens per ingestion job
CHARS_PER_TOKEN = 4      # rough chars-per-token estimate

# System prompt used by the memory extractor during BEAM ingestion
_EVAL_SYSTEM_PROMPT = """\
You are the MindCache Extraction Engine running in EXHAUSTIVE MODE.

Your goal: REPRODUCE all substantive content from this conversation as structured memory entries.
This is NOT summarization. You must capture every piece of information, no matter the domain.

### INPUT FORMAT
The conversation is presented as alternating turns prefixed with "User:" and "Assistant:".
A "Timestamp:" header marks the session date. Extract from BOTH sides.
- User-stated details: prefix with "User-stated (fact): "
- LLM-suggested (unverified): prefix with "LLM-suggested (unverified by user): "

### RULES
**"reasoning"** → lightweight planning step. List key sub-topics and memory types needed.
**"topics_root"** → 1-2 high-level domains, e.g. ["Sleep Science"] or ["Machine Learning"].
**"topics_branch"** → sub-path within topics_root. Domain > Sub-domain > Specific Concept.
**"memory"** → one bucket per sub-topic. ALL content goes here.

### WHAT TO EXTRACT
[FACT] — Most important. Capture every definition, fact, number, procedure, recommendation,
         correction, comparison, research finding, tool, and product mentioned.
         Prefix user-stated facts with "User-stated (fact): ".
         Prefix LLM-suggested unverified facts with "LLM-suggested (unverified by user): ".
         Target: at least 3 fact entries per turn pair. No upper limit.
[USER] — Every preference, instruction, personal context, biographical fact, relationship.
[EPIS] — What was discussed and how the conversation progressed.
[DECISION] — Only meaningful choices with lasting impact.

### WHAT TO STRIP
Filler: "ok", "thanks", "I see", greetings, meta-conversation.

### WRITING STYLE
Retrieval-friendly: each entry must stand alone. Include all specifics inline.
Preserve ALL numbers, dates, names, technical terms, and proper nouns.

### OUTPUT
Valid JSON matching the ChatExtraction schema.
"""


def _clean_text(text: str) -> str:
    """Sanitize raw BEAM content — prevents LLM token repetition loops."""
    text = text.replace('\t', ' ')
    text = _re.sub(r'\n{3,}', '\n\n', text)
    text = _re.sub(r' {3,}', ' ', text)
    return text.strip()


def _normalize_for_ratio(text: str) -> str:
    text = text.replace('\t', ' ').replace('\n', ' ')
    return _re.sub(r' {2,}', ' ', text).strip()


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _format_session_text(session: dict) -> str:
    lines = ["Timestamp: " + str(session["timestamp"])]
    for turn in session["content"]:
        role = turn.get("role", "unknown").capitalize()
        lines.append(f"{role}: {_clean_text(turn.get('content', ''))}")
    lines.append("")
    return "\n".join(lines)


def _format_turns_text(turns: list, timestamp: str) -> str:
    lines = ["Timestamp: " + str(timestamp)]
    for turn in turns:
        role = turn.get("role", "unknown").capitalize()
        lines.append(f"{role}: {_clean_text(turn.get('content', ''))}")
    lines.append("")
    return "\n".join(lines)


def _partition_text(text: str, target_chars: int = 4000) -> list:
    """
    Partition a conversation log into ~target_chars chunks of complete
    (User, Assistant) turn pairs. Used for multi-vector embedding.
    """
    from mindcache.Database.db_manager import DatabaseManager
    clean = DatabaseManager._strip_timestamps(text)
    lines = clean.split("\n")

    chunks, current, current_len = [], [], 0
    for line in lines:
        current.append(line)
        current_len += len(line) + 1
        if current_len >= target_chars and line.lower().startswith("assistant:"):
            chunks.append("\n".join(current))
            current, current_len = [], 0
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Adapt BEAM parquet → MindCache JSON
# ─────────────────────────────────────────────────────────────────────────────

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

    r    = df.iloc[conv_index]
    conv_id = r['conversation_id']
    chat    = r['chat']
    seed    = r['conversation_seed']

    def _flatten_session(sess):
        turns, seen = [], set()
        if isinstance(sess, dict):
            for _, batches in sess.items():
                if not batches:
                    continue
                for batch in batches:
                    if not isinstance(batch, dict):
                        continue
                    for tl in batch.get('turns', []):
                        if isinstance(tl, dict):
                            tl = [tl]
                        for t in tl:
                            if isinstance(t, dict) and 'id' in t and t['id'] not in seen:
                                seen.add(t['id'])
                                turns.append(t)
        return turns

    flat = [_flatten_session(s) for s in chat]
    log.info(
        f"[Stage 1] Conv #{conv_id}: '{seed.get('title','?')}' "
        f"({seed.get('category','?')}) — "
        f"{len(chat)} sessions, {sum(len(s) for s in flat)} turns"
    )

    turn_to_session = {t['id']: si for si, turns in enumerate(flat) for t in turns}

    sessions = []
    for si, turns in enumerate(flat):
        ts = next((t['time_anchor'] for t in turns if t.get('time_anchor')), None)
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

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    log.info(
        f"[Stage 1] Saved → {output_path}  "
        f"({len(sessions)} sessions, {len(test_cases)} test cases)"
    )
    return output_data


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Ingest adapted JSON into MindCache DB
# ─────────────────────────────────────────────────────────────────────────────

def _save_job(db_session, batch_texts, batch_meta):
    """Persist a batch of session text as a ProcessingJob row."""
    from mindcache.Database.db_setup import ProcessingJob

    if isinstance(batch_meta, list):
        ids = [m["id"] for m in batch_meta]
        ts  = next((m["timestamp"] for m in batch_meta if m.get("timestamp")), None)
    else:
        ids = batch_meta.get("id", [])
        ts  = batch_meta.get("timestamp")

    timestamp = None
    if ts:
        for fmt in ("%B-%d-%Y", None):
            try:
                timestamp = datetime.strptime(ts, fmt) if fmt else datetime.fromisoformat(ts)
                break
            except Exception:
                continue

    prompt_text = "\n".join(batch_texts) if isinstance(batch_texts, list) else batch_texts
    job = ProcessingJob(
        raw_prompt     = prompt_text,
        raw_response   = json.dumps(batch_meta),
        raw_next_prompt= json.dumps(ids),
        status         = "pending",
        timestamp      = timestamp,
    )
    db_session.add(job)
    db_session.flush()


def _create_jobs(adapted_data: dict, db_path: str):
    """
    Batch sessions from the adapted JSON into ProcessingJob rows.
    Resets the DB first (drop + re-create tables).
    Handles both small sessions (batched together) and large BEAM sessions
    (split across multiple jobs at TOKEN_BUDGET boundaries).
    """
    from mindcache.Database.db_setup import init_db, engine, Base, ProcessingJob
    from sqlalchemy.orm import sessionmaker

    sessions = adapted_data.get("sessions", [])
    log.info(f"[Stage 2] {len(sessions)} sessions → creating jobs (DB reset first)")

    Base.metadata.drop_all(engine)
    init_db()

    Session = sessionmaker(bind=engine)
    db_session = Session()

    batch_texts, batch_meta, batch_tokens, job_count = [], [], 0, 0

    for session in sessions:
        sid      = session["id"]
        ts       = session.get("timestamp")
        content  = session.get("content", [])
        sess_txt = _format_session_text(session)
        sess_tok = _estimate_tokens(sess_txt)

        if sess_tok <= TOKEN_BUDGET:
            if batch_tokens + sess_tok > TOKEN_BUDGET and batch_texts:
                _save_job(db_session, batch_texts, batch_meta)
                job_count += 1
                batch_texts, batch_meta, batch_tokens = [], [], 0
            batch_texts.append(sess_txt)
            batch_meta.append({"id": sid, "timestamp": ts})
            batch_tokens += sess_tok
        else:
            if batch_texts:
                _save_job(db_session, batch_texts, batch_meta)
                job_count += 1
                batch_texts, batch_meta, batch_tokens = [], [], 0

            chunk_turns, chunk_tokens = [], 0
            for turn in content:
                turn_txt = f"{turn.get('role','user').capitalize()}: {turn.get('content','')}"
                turn_tok = _estimate_tokens(turn_txt)
                if (chunk_tokens + turn_tok > TOKEN_BUDGET
                        and chunk_turns
                        and chunk_turns[-1].get("role") == "assistant"):
                    chunk_text = _format_turns_text(chunk_turns, ts)
                    turn_ids   = [t.get("turn_id") for t in chunk_turns if t.get("turn_id") is not None]
                    _save_job(db_session, chunk_text, {"id": turn_ids, "timestamp": ts})
                    job_count += 1
                    chunk_turns, chunk_tokens = [], 0
                chunk_turns.append(turn)
                chunk_tokens += turn_tok

            if chunk_turns:
                turn_ids  = [t.get("turn_id") for t in chunk_turns if t.get("turn_id") is not None]
                chunk_text = _format_turns_text(chunk_turns, ts)
                _save_job(db_session, chunk_text, {"id": turn_ids, "timestamp": ts})
                job_count += 1

    if batch_texts:
        _save_job(db_session, batch_texts, batch_meta)
        job_count += 1

    db_session.commit()
    db_session.close()
    log.info(f"[Stage 2] Created {job_count} processing jobs (token budget: {TOKEN_BUDGET})")


def _batch_embed(pending, embedder):
    """Pre-compute multi-vector embeddings for all pending jobs that lack one."""
    import numpy as np
    from mindcache.Database.db_setup import engine, ProcessingJob
    from sqlalchemy.orm import sessionmaker

    needs = [j for j in pending if j.embedding is None]
    if not needs:
        log.info(f"[Embed] All {len(pending)} jobs already embedded.")
        return

    log.info(f"[Embed] Embedding {len(needs)} jobs...")
    Session = sessionmaker(bind=engine)
    db_session = Session()
    try:
        for job in needs:
            parts = _partition_text(job.raw_prompt)
            if not parts:
                continue
            vecs = embedder.encode(parts, is_query=False)
            job.embedding = vecs.astype(np.float32).tobytes()
            db_job = db_session.get(ProcessingJob, job.id)
            if db_job:
                db_job.embedding = job.embedding
            db_session.commit()
            log.info(f"[Embed] Job {job.id}: {len(parts)} chunks embedded.")
    except Exception as e:
        db_session.rollback()
        log.warning(f"[Embed] Batch embedding failed ({e}). Will embed on-demand.")
    finally:
        db_session.close()


def _run_single_batch(limit: int = 35):
    """
    Process up to `limit` pending jobs: extract memories via Gemini, save to DB,
    run tree reorganization. Called as a subprocess by _run_jobs() for memory isolation.
    """
    from mindcache.Database.db_setup import engine, ProcessingJob
    from mindcache.Database.db_manager import DatabaseManager
    from mindcache.Memory_extract.memory_extractor import Memory_Extractor
    from mindcache.Memory_extract.safe_ai import AllKeysExhaustedError
    from mindcache.Database.reorganize_tree import reorganize_tree
    from sqlalchemy.orm import sessionmaker

    try:
        extractor = Memory_Extractor(sys_prompt=_EVAL_SYSTEM_PROMPT)
        log.info(f"Memory Extractor ready (model: {extractor.engine.model_name})")
    except Exception as e:
        log.error(f"Failed to init Memory Extractor: {e}")
        log.error("Make sure GEMINI_API_KEY is set.")
        return

    Session    = sessionmaker(bind=engine)
    db_session = Session()
    pending    = (db_session.query(ProcessingJob)
                  .filter(ProcessingJob.status == "pending")
                  .order_by(ProcessingJob.id).limit(limit).all())

    if not pending:
        log.info("No pending jobs in this batch.")
        db_session.close()
        return

    _batch_embed(pending, extractor._db._get_embedder())
    db_session.expire_all()
    pending = (db_session.query(ProcessingJob)
               .filter(ProcessingJob.status == "pending",
                       ProcessingJob.id.in_([j.id for j in pending]))
               .order_by(ProcessingJob.id).all())

    db_manager  = DatabaseManager()
    success = failed = 0
    MAX_RETRIES, MIN_RATIO = 3, 5

    for i, job in enumerate(pending):
        turn_ids = json.loads(job.raw_next_prompt)
        log.info(f"[Job {i+1}/{len(pending)}] {len(turn_ids)} turns")
        try:
            t0 = time.time()
            extracted = extractor.memory_extract(job.raw_prompt, query_embedding=job.embedding)
            elapsed   = time.time() - t0

            if not extracted:
                log.warning(f"  [FAIL] No extraction ({elapsed:.1f}s)")
                job.status = "failed"; job.retry_count += 1
                db_session.commit(); failed += 1
                continue

            input_tok = len(_normalize_for_ratio(job.raw_prompt)) // 4
            mem_chars = sum(
                len(e) for b in extracted.get("memory", [])
                for mt in ["user","fact","epis","decision"]
                for e in b.get(mt, []) if isinstance(e, str)
            )
            mem_count = sum(
                len(b.get(mt, [])) for b in extracted.get("memory", [])
                for mt in ["user","fact","epis","decision"]
            )
            ratio = (mem_chars // 4) / input_tok * 100 if input_tok > 0 else 0

            if ratio < MIN_RATIO and job.retry_count < MAX_RETRIES:
                job.retry_count += 1; db_session.commit(); failed += 1
                log.warning(f"  [LOW] {mem_count} memories, ratio={ratio:.0f}% < {MIN_RATIO}%. Retry {job.retry_count}/{MAX_RETRIES}")
                continue

            db_manager.save_extracted_memory(
                job_id=job.id, raw_msg=job.raw_prompt,
                extracted_data=extracted,
                source_session_id=json.dumps(turn_ids),
                session_timestamp=job.timestamp,
            )
            db_session.expire_all()
            if db_session.get(ProcessingJob, job.id):
                log.warning(f"  [WARN] Save may have failed silently ({elapsed:.1f}s)")
                failed += 1
            else:
                log.info(f"  [OK] {mem_count} memories, ratio={ratio:.0f}% ({elapsed:.1f}s)")
                success += 1

        except AllKeysExhaustedError:
            log.error("ALL API KEYS EXHAUSTED — stopping ingestion.")
            db_session.close()
            sys.exit(8)
        except Exception as e:
            msg = str(e)
            log.error(f"  [ERROR] {msg[:200]}")
            if any(x in msg.lower() for x in ["quota", "429", "resource_exhausted"]):
                log.error("API QUOTA EXHAUSTED — stopping ingestion.")
                db_session.close()
                sys.exit(8)
            job.status = "failed"; job.retry_count += 1
            db_session.commit(); failed += 1

    db_session.close()
    log.info(f"Batch done: {success} succeeded, {failed} failed.")

    if success > 0:
        log.info("Running tree reorganization...")
        try:
            reorganize_tree(dry_run=False)
        except Exception as e:
            log.warning(f"Reorganization error: {e}")


def _run_jobs():
    """
    Manager: repeatedly spawns this script as a subprocess with --_run-batch
    to process all pending jobs in groups of 35, ensuring full memory cleanup
    between batches (important when running large Gemini model workloads).
    """
    from mindcache.Database.db_setup import engine, ProcessingJob
    from sqlalchemy.orm import sessionmaker

    Session    = sessionmaker(bind=engine)
    total_done = 0
    batch_num  = 1

    while True:
        db_session    = Session()
        pending_count = db_session.query(ProcessingJob).filter(
            ProcessingJob.status == "pending").count()
        db_session.close()

        if pending_count == 0:
            log.info("No more pending jobs.")
            break

        log.info(f"=== BATCH {batch_num}: spawning subprocess for up to 35 jobs ===")
        script = os.path.abspath(__file__)
        try:
            subprocess.run([sys.executable, "-u", script, "--_run-batch"], check=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == 8:
                sys.exit(8)
            sys.exit(e.returncode)

        db_session    = Session()
        new_pending   = db_session.query(ProcessingJob).filter(
            ProcessingJob.status == "pending").count()
        db_session.close()

        processed = pending_count - new_pending
        if processed <= 0:
            log.warning("No jobs processed in this batch — stopping to avoid loop.")
            break

        total_done += processed
        batch_num  += 1
        import gc; gc.collect()
        time.sleep(2)

    log.info(f"All batches done. Total processed: {total_done}")


def ingest_adapted(adapted_data: dict, db_path: str):
    """
    Full ingestion pipeline:
      1. Create ProcessingJob rows from sessions (no API calls)
      2. Run memory extraction via Gemini (spawns subprocess batches)

    Requires GEMINI_API_KEY in environment.
    """
    _create_jobs(adapted_data, db_path)
    log.info("[Stage 2] Starting extraction (this may take several minutes)…")
    _run_jobs()
    log.info("[Stage 2] Ingestion complete.")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Retrieval evaluation
# ─────────────────────────────────────────────────────────────────────────────

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
    from mindcache import MindCache
    from mindcache.retrieval.root_cache import refresh_tree_cache

    os.makedirs(output_dir, exist_ok=True)
    if limit:
        test_cases = test_cases[:limit]

    log.info(f"[Stage 3] Initialising MindCache (db={db_path}, user={user_id})")
    mc = MindCache(db_path=db_path)
    # Refresh so no stale in-memory/disk cache pollutes results
    refresh_tree_cache(user_id=user_id)

    results = []
    for i, tc in enumerate(test_cases):
        q_id     = tc.get("question_id",   f"q_{i}")
        q_type   = tc.get("question_type", "unknown")
        q_text   = tc.get("question",      "")
        expected = tc.get("answer", tc.get("expected", ""))
        rubric   = tc.get("rubric",        [])

        log.info(f"[Stage 3] [{i+1}/{len(test_cases)}] {q_text[:80]}…")

        t0  = time.time()
        res = mc.retriever.retrieve(q_text, user_id=user_id, use_reranker=False)
        lat = round(time.time() - t0, 3)

        log.info(
            f"  → context={len(res.context)} chars | "
            f"type={res.query_type or q_type} | latency={lat}s"
        )
        results.append({
            "question_id":       q_id,
            "question_type":     q_type,
            "detected_type":     res.query_type or q_type,
            "question":          q_text,
            "expected":          expected,
            "rubric":            rubric,
            "system_prompt":     res.system_hint,
            "retrieved_context": res.context,
            "retrieval_latency": lat,
        })
        time.sleep(0.5)

    out_path = os.path.join(output_dir, f"beam_conv{conv_id}_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info(f"[Stage 3] Results saved → {out_path}  ({len(results)} questions)")
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="End-to-end BEAM evaluation for MindCache (adapt → ingest → retrieve).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parquet",       default="BEAM/1M-00000-of-00001.parquet")
    parser.add_argument("--conv-index",    type=int, default=7)
    parser.add_argument("--db-path",       default="mindcache.db")
    parser.add_argument("--output-dir",    default="results")
    parser.add_argument("--user-id",       default="default")
    parser.add_argument("--adapted-json",  default=None)
    parser.add_argument("--skip-ingest",   action="store_true")
    parser.add_argument("--limit",         type=int, default=None)
    # Internal flag used by _run_jobs() subprocess spawning — not for end users
    parser.add_argument("--_run-batch",    action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    # ── Internal subprocess entry-point ──────────────────────────────────────
    if args._run_batch:
        _run_single_batch(limit=35)
        sys.exit(0)

    # ── Normal pipeline ──────────────────────────────────────────────────────
    os.makedirs(args.output_dir, exist_ok=True)
    if args.adapted_json is None:
        args.adapted_json = os.path.join(
            args.output_dir, f"beam_adapted_conv{args.conv_index}.json"
        )

    if not args.skip_ingest:
        adapted_data = adapt_beam(args.parquet, args.adapted_json, args.conv_index)
        ingest_adapted(adapted_data, args.db_path)
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
