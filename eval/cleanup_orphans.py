"""
Unified cleanup script: detects orphan topics AND detached memories,
traces every affected TriadBlock (1 TriadBlock = 1 job = 1 LLM run),
fully wipes everything that job produced (memories, registry, embeddings,
TriadBlocks, orphan topics), and creates new jobs for clean reprocessing.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import (
    engine, Topic, TopicEmbeddingCache,
    EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory,
    TriadBlock, MemoryRegistry, ProcessingJob
)

MEMORY_TABLES = [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]
DATASET_PATH = "eval/data/longmemeval_m_merged.json"


def run_cleanup():
    Session = sessionmaker(bind=engine)
    session = Session()

    # ── 1. Detect problems ──────────────────────────────────────────────
    # 1a. Orphan topics: have a level but lost their parent
    orphans = session.query(Topic).filter(
        Topic.parent_id.is_(None),
        Topic.level > 0
    ).all()
    orphan_ids = {t.id for t in orphans}
    print(f"[1a] Found {len(orphan_ids)} orphan topics (parent_id=NULL, level > 0)")

    # 1b. Detached memories: extracted but never assigned a topic
    detached_count = 0
    for Mem in MEMORY_TABLES:
        c = session.query(Mem).filter(Mem.topic_id.is_(None)).count()
        detached_count += c
    print(f"[1b] Found {detached_count} detached memories (topic_id=NULL)")

    if not orphan_ids and detached_count == 0:
        print("\nNothing to clean up!")
        session.close()
        return

    # ── 2. Trace to the bad TriadBlocks (1 TriadBlock = 1 job) ──────────
    # Collect message_ids from memories on orphan topics + detached memories
    bad_msg_ids = set()
    for Mem in MEMORY_TABLES:
        rows = session.query(Mem.message_id).filter(Mem.topic_id.in_(orphan_ids)).all()
        bad_msg_ids.update(r[0] for r in rows if r[0])

        rows = session.query(Mem.message_id).filter(Mem.topic_id.is_(None)).all()
        bad_msg_ids.update(r[0] for r in rows if r[0])

    print(f"\n[2] Traced to {len(bad_msg_ids)} TriadBlocks to wipe")

    if not bad_msg_ids:
        print("    Could not trace to any TriadBlocks — nothing to wipe.")
        session.close()
        return

    # ── 3. Collect everything produced by those TriadBlocks ─────────────
    all_memory_ids = set()
    all_topic_ids = set()
    for Mem in MEMORY_TABLES:
        rows = session.query(Mem.id, Mem.topic_id).filter(
            Mem.message_id.in_(bad_msg_ids)
        ).all()
        for mem_id, topic_id in rows:
            all_memory_ids.add(mem_id)
            if topic_id:
                all_topic_ids.add(topic_id)

    # Include orphan topic ids
    all_topic_ids.update(orphan_ids)

    # Parse source_session_id JSON to get individual session IDs for job re-creation
    affected_session_ids = set()
    msgs = session.query(TriadBlock.source_session_id).filter(
        TriadBlock.id.in_(bad_msg_ids)
    ).all()
    for (ssid,) in msgs:
        if ssid:
            try:
                sids = json.loads(ssid)
                if isinstance(sids, list):
                    affected_session_ids.update(sids)
                else:
                    affected_session_ids.add(str(sids))
            except (json.JSONDecodeError, TypeError):
                affected_session_ids.add(str(ssid))

    print(f"\n[3] Full wipeout scope:")
    print(f"    TriadBlocks:  {len(bad_msg_ids)}")
    print(f"    Memories:     {len(all_memory_ids)}")
    print(f"    Topics:       {len(all_topic_ids)}")
    print(f"    Session IDs:  {len(affected_session_ids)}")

    # ── 4. Confirm ──────────────────────────────────────────────────────
    confirm = input(f"\nWipe {len(all_memory_ids)} memories, {len(bad_msg_ids)} messages, "
                    f"{len(orphan_ids)} orphan topics, embeddings, "
                    f"and re-create jobs for {len(affected_session_ids)} sessions? [y/N] ")
    if confirm.lower() != 'y':
        print("Aborted.")
        session.close()
        return

    # ── 5. Execute wipeout ──────────────────────────────────────────────
    # 5a. Delete memories from all 4 tables
    for Mem in MEMORY_TABLES:
        count = session.query(Mem).filter(
            Mem.message_id.in_(bad_msg_ids)
        ).delete(synchronize_session=False)
        if count:
            print(f"    Deleted {count} from {Mem.__tablename__}")

    # 5b. Delete memory registry entries
    if all_memory_ids:
        session.query(MemoryRegistry).filter(
            MemoryRegistry.id.in_(all_memory_ids)
        ).delete(synchronize_session=False)
        print(f"    Deleted {len(all_memory_ids)} memory registry entries")

    # 5c. Delete embedding cache for affected topics
    if all_topic_ids:
        ec = session.query(TopicEmbeddingCache).filter(
            TopicEmbeddingCache.topic_leaf_id.in_(all_topic_ids)
        ).delete(synchronize_session=False)
        print(f"    Deleted {ec} embedding cache entries")

    # 5d. Delete orphan topics
        session.query(Topic).filter(
            Topic.id.in_(all_topic_ids)
        ).delete(synchronize_session=False)
        print(f"    Deleted {len(all_topic_ids)} orphan topics")

    # 5e. Delete TriadBlocks
    session.query(TriadBlock).filter(
        TriadBlock.id.in_(bad_msg_ids)
    ).delete(synchronize_session=False)
    print(f"    Deleted {len(bad_msg_ids)} TriadBlock messages")

    """# ── 6. Create new jobs for affected sessions ────────────────────────
    if affected_session_ids and os.path.exists(DATASET_PATH):
        print(f"\n[6] Creating new jobs from {DATASET_PATH}...")
        with open(DATASET_PATH, "r", encoding="utf-8") as f:
            dataset = json.load(f)

        sessions_to_redo = [s for s in dataset.get("sessions", [])
                            if s["id"] in affected_session_ids]

        if sessions_to_redo:
            from eval.ingest_api import format_session_text, _save_job

            TOKEN_BUDGET = 5_000
            CHARS_PER_TOKEN = 4
            current_batch_text = []
            current_batch_meta = []
            current_tokens = 0
            job_count = 0

            for s in sessions_to_redo:
                text = format_session_text(s)
                tokens = len(text) // CHARS_PER_TOKEN

                if current_tokens + tokens > TOKEN_BUDGET and current_batch_text:
                    _save_job(session, current_batch_text, current_batch_meta)
                    job_count += 1
                    current_batch_text = []
                    current_batch_meta = []
                    current_tokens = 0

                current_batch_text.append(text)
                current_batch_meta.append({"id": s["id"], "timestamp": s.get("timestamp")})
                current_tokens += tokens

            if current_batch_text:
                _save_job(session, current_batch_text, current_batch_meta)
                job_count += 1

            print(f"    Created {job_count} new jobs for {len(sessions_to_redo)} sessions")
        else:
            print(f"    WARNING: None of the affected session IDs found in dataset")
    elif affected_session_ids:
        print(f"\n[6] Dataset not found at {DATASET_PATH} — cannot create jobs")
        print(f"    Save these session IDs for manual re-ingestion:")
        for sid in sorted(affected_session_ids):
            print(f"      {sid}")"""

    session.commit()
    session.close()
    print("\n[DONE] Full wipeout complete. Run `python eval/ingest_api.py run` to reprocess.")


if __name__ == "__main__":
    run_cleanup()
