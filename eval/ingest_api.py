import json
import argparse
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.db_setup import init_db, engine, Base, ProcessingJob
from Database.db_manager import DatabaseManager
from Memory_extract.memory_extractor import Memory_Extractor
from Memory_extract.safe_ai import AllKeysExhaustedError
from sqlalchemy.orm import sessionmaker

# ─── Eval-Specific Exhaustive Extraction Prompt ──────────────────────────────
# The default MindCache prompt extracts concise atomic memories from large LLM
# conversations. But LongMemEval sessions are dense info haystacks — every name,
# date, number, location, and relationship could be the answer to a test question.
# This prompt tells the LLM to extract EVERYTHING, no matter how small.
# ─────────────────────────────────────────────────────────────────────────────
EVAL_SYSTEM_PROMPT = """You are the MindCache Extraction Engine running in EXHAUSTIVE MODE for evaluation.

Your goal: Extract EVERY piece of information from the conversation. Miss nothing.

### WHY EXHAUSTIVE MODE
This conversation is part of a memory benchmark test. A question will later ask about ANY detail mentioned here — a name, a date, a number, a place, a process, a preference, a recommendation. If you skip it, the system fails the test. Extract aggressively.

### EXTRACTION RULES

**FIELD 1: "reasoning" (Phase A)**
- Scan the ENTIRE conversation systematically.
- For each turn, list ALL extractable facts — even small ones.
- Classify as 'user', 'fact', 'epis', or 'decision'.

**FIELD 2: "topics_root" (Global Context)**
- The high-level domain(s) covering this conversation.
- Example: ["Travel", "Orlando"] or ["Work", "Shift Schedule"].

**FIELD 3: "memory" (Data Buckets)**
- Create MULTIPLE buckets — one per sub-topic discussed.
- Inside each bucket, extract:

[topics_branch] → Sub-path for routing.
  If existing topics are listed below, reuse exact names.

[USER] → Preferences, profile, personal details.
  - Names, relationships, preferences, habits, plans, experiences.
  - e.g., "User's name is Alex," "User plans to visit Bandung in March,"
    "User has 3 dogs," "User prefers window seats."

[FACT] → EVERY concrete detail. This is the most critical bucket.
  - Names of people, places, businesses, products, tools.
  - Numbers: dates, times, prices, quantities, measurements.
  - Processes: how things work, step-by-step procedures.
  - Relationships: who works where, what connects to what.
  - Specific recommendations and their details.
  - e.g., "The Sugar Factory is at Icon Park, Orlando, known for giant milkshakes."
  - e.g., "Admon works the 8am-4pm Day Shift on Sundays."
  - e.g., "CITGO's Lake Charles Refinery processes 425,000 barrels/day."
  - DO NOT summarize multiple facts into one. Keep them SEPARATE and GRANULAR.
  - One fact = one specific piece of information.

[EPIS] → What happened / what was discussed.
  - Log the narrative: "User asked about shift schedules," "AI provided restaurant list."

[DECISION] → Reasoning behind choices.
  - e.g., "Chose SQLite over MongoDB due to memory weight."

### CRITICAL INSTRUCTIONS
1. Extract MORE rather than less. When in doubt, EXTRACT IT.
2. Keep facts GRANULAR — one fact per string entry, not combined paragraphs.
3. Include specific names, numbers, dates, and identifiers — never generalize them away.
4. If a list is mentioned (e.g., schedule, menu items, steps), extract EACH item separately.
5. Your output must be VALID JSON matching the ChatExtraction schema.
"""

# Config 
TOKEN_BUDGET = 5_000           # ~5k tokens per batch for high extraction recall
CHARS_PER_TOKEN = 4            # Rough chars-to-tokens ratio


def format_session_text(session: dict) -> str:
    """Convert a session dict (id + content list of turns) into prompt text."""
    session_ts = session["timestamp"]
    content = session["content"]
    
    lines = ["Timestamp: " + str(session_ts)]
    for turn in content:
        role = turn.get("role", "unknown").capitalize()
        text = turn.get("content", "")
        lines.append(f"{role}: {text}")
    lines.append("")  # blank line separator
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count."""
    return len(text) // CHARS_PER_TOKEN
    

# PHASE A: CREATE JOBS
def create_jobs(input_path: str, reset: bool = False):
    """Batch sessions into ProcessingJob rows. No API calls."""
    print(f"Loading dataset from {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    sessions = data.get("sessions", [])
    print(f"Found {len(sessions)} sessions to batch.")
    
    if reset:
        print("Resetting database...")
        Base.metadata.drop_all(engine)
        init_db()
        print("Database reset complete.")
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    # Batch sessions into jobs
    current_batch_text = []
    current_batch_meta = []   # [{"id": ..., "timestamp": ...}, ...]
    current_tokens = 0
    job_count = 0
    
    for session in sessions:
        session_text = format_session_text(session)
        session_tokens = estimate_tokens(session_text)
        
        # Check if adding this session would exceed budget
        if current_tokens + session_tokens > TOKEN_BUDGET and current_batch_text:
            # Save current batch as a job
            _save_job(db_session, current_batch_text, current_batch_meta)
            job_count += 1
            current_batch_text = []
            current_batch_meta = []
            current_tokens = 0
        
        current_batch_text.append(session_text)
        current_batch_meta.append({
            "id": session["id"],
            "timestamp": session.get("timestamp", None)
        })
        current_tokens += session_tokens
    
    # Save remaining batch
    if current_batch_text:
        _save_job(db_session, current_batch_text, current_batch_meta)
        job_count += 1
    
    db_session.commit()
    db_session.close()
    
    print(f"\nCreated {job_count} processing jobs.")
    print(f"Average sessions per job: {len(sessions) / max(job_count, 1):.1f}")


def _save_job(db_session, batch_texts: list, batch_meta: list):
    """Save a batch of sessions as a ProcessingJob.
    batch_meta: [{"id": "session_id", "timestamp": "2024-01-15T10:30:00"}, ...]
    """
    job = ProcessingJob(
        raw_prompt="\n".join(batch_texts),
        raw_response=json.dumps(batch_meta),  # Store session IDs + timestamps
        raw_next_prompt="",
        status="pending",
        timestamp=datetime.now()
    )
    db_session.add(job)
    db_session.flush()


def run_jobs(limit: int = None):
    """Process pending jobs via Memory_Extractor."""
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    pending = db_session.query(ProcessingJob).filter(
        ProcessingJob.status == "pending"
    ).order_by(ProcessingJob.id).all()
    
    if not pending:
        print("No pending jobs. Run `create` first or all jobs are done.")
        db_session.close()
        return
    
    if limit:
        pending = pending[:limit]
    
    print(f"Found {len(pending)} pending jobs to process.")
    
    try:
        extractor = Memory_Extractor(sys_prompt=EVAL_SYSTEM_PROMPT)
        print(f"Memory Extractor initialized in EXHAUSTIVE MODE (model: {extractor.engine.model_name})")
    except Exception as e:
        print(f"Failed to initialize Memory Extractor: {e}")
        print("Set GEMINI_API_KEY environment variable and retry.")
        db_session.close()
        return
    
    db_manager = DatabaseManager()
    
    success = 0
    failed = 0
    
    for i, job in enumerate(pending):
        batch_meta = json.loads(job.raw_response)
        # Handle both old format ["id", ...] and new format [{"id": ..., "timestamp": ...}, ...]
        if batch_meta and isinstance(batch_meta[0], str):
            batch_meta = [{"id": sid, "timestamp": None} for sid in batch_meta]
        
        session_ids = [m["id"] for m in batch_meta]
        print(f"\n[Job {i+1}/{len(pending)}] Processing {len(session_ids)} sessions: {session_ids[:3]}{'...' if len(session_ids) > 3 else ''}")
        
        try:
            start_time = time.time()
            extracted_data = extractor.memory_extract(job.raw_prompt)
            elapsed = time.time() - start_time
            
            if not extracted_data:
                print(f"  [FAIL] No extraction result ({elapsed:.1f}s)")
                job.status = "failed"
                job.retry_count += 1
                db_session.commit()
                failed += 1
                continue
            
            # Use the earliest session timestamp from this batch
            session_ts = None
            for m in batch_meta:
                if m.get("timestamp"):
                    session_ts = m["timestamp"]
                    break  # Use first available timestamp
            
            db_manager.save_extracted_memory(
                job_id=job.id,
                raw_msg=f"[Batched extraction] {len(session_ids)} sessions",
                extracted_data=extracted_data,
                source_session_id=json.dumps(session_ids),
                session_timestamp=session_ts
            )
            
            db_session.commit()
            
            mem_count = sum(
                len(b.get("user", [])) + len(b.get("fact", [])) + 
                len(b.get("epis", [])) + len(b.get("decision", []))
                for b in extracted_data.get("memory", [])
            )
            print(f"  [OK] {mem_count} memories extracted ({elapsed:.1f}s)")
            success += 1
            
        except AllKeysExhaustedError:
            print(f"\n{'='*60}")
            print("ALL PROVIDED API KEYS EXHAUSTED!")
            print(f"Processed {success} jobs before hard exit. {len(pending) - i} remaining.")
            print(f"{'='*60}")
            db_session.close()
            # Exit with specific code so orchestrator knows why
            sys.exit(8)

        except Exception as e:
            error_msg = str(e)
            print(f"  [ERROR] {error_msg[:200]}")
            
            if "quota" in error_msg.lower() or "429" in error_msg or "resource_exhausted" in error_msg.lower():
                print(f"\n{'='*60}")
                print("API QUOTA EXHAUSTED!")
                print(f"{'='*60}")
                print(f"\nProcessed {success} jobs before quota hit. {len(pending) - i - 1} remaining.")
                db_session.close()
                sys.exit(8)
            
            job.status = "failed"
            job.retry_count += 1
            db_session.commit()
            failed += 1
    
    db_session.close()
    print(f"\nDone: {success} succeeded, {failed} failed.")
    remaining = db_session.query(ProcessingJob).filter(ProcessingJob.status == "pending").count() if success > 0 else len(pending) - success
    print(f"Remaining pending: check with `python eval/ingest_api.py status`")


# STATUS CHECK
def show_status():
    """Show current job queue status."""
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    total = db_session.query(ProcessingJob).count()
    pending = db_session.query(ProcessingJob).filter(ProcessingJob.status == "pending").count()
    done = db_session.query(ProcessingJob).filter(ProcessingJob.status == "done").count()
    failed = db_session.query(ProcessingJob).filter(ProcessingJob.status == "failed").count()
    
    print(f"Processing Queue Status:")
    print(f"  Total:   {total}")
    print(f"  Pending: {pending}")
    print(f"  Done:    {done}")
    print(f"  Failed:  {failed}")
    
    if pending > 0:
        print(f"\nTo process: python eval/ingest_api.py run")
    if failed > 0:
        print(f"To retry failed: python eval/ingest_api.py retry")
    
    db_session.close()


def retry_failed():
    """Reset failed jobs back to pending."""
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    count = db_session.query(ProcessingJob).filter(
        ProcessingJob.status == "failed"
    ).update({"status": "pending"})
    db_session.commit()
    db_session.close()
    print(f"Reset {count} failed jobs to pending.")


# CLI
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Queue-based API ingestion for LongMemEval-M")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # create
    create_parser = subparsers.add_parser("create", help="Create processing jobs from dataset")
    create_parser.add_argument("--input", default="eval/data/longmemeval_m_merged.json")
    create_parser.add_argument("--reset", action="store_true", help="Reset DB before creating jobs")
    
    # run
    run_parser = subparsers.add_parser("run", help="Process pending jobs via Gemini API")
    run_parser.add_argument("--limit", type=int, default=None, help="Max jobs to process")
    
    # status
    subparsers.add_parser("status", help="Show job queue status")
    
    # retry
    subparsers.add_parser("retry", help="Reset failed jobs to pending")
    
    args = parser.parse_args()
    
    if args.command == "create":
        create_jobs(args.input, args.reset)
    elif args.command == "run":
        run_jobs(args.limit)
    elif args.command == "status":
        show_status()
    elif args.command == "retry":
        retry_failed()
    else:
        parser.print_help()
