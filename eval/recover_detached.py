import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.db_setup import engine, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory, TriadBlock, MemoryRegistry, ProcessingJob
from sqlalchemy.orm import sessionmaker

def recover():
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Collect all message_ids from memories where topic_id IS NULL
    message_ids = set()
    for table_cls in [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]:
        records = session.query(table_cls.message_id).filter(table_cls.topic_id.is_(None)).all()
        for (m_id,) in records:
            if m_id:
                message_ids.add(m_id)
                
    if not message_ids:
        print("No detached memories found.")
        return

    print(f"Found {len(message_ids)} messages containing detached memories.")

    # 2. Get the unique source_session_ids (these are JSON strings of session IDs)
    messages = session.query(TriadBlock).filter(TriadBlock.id.in_(message_ids)).all()
    source_sessions = set()
    for msg in messages:
        if msg.source_session_id:
            source_sessions.add(msg.source_session_id)
            
    print(f"Associated with {len(source_sessions)} batched session sets.")

    # 3. Find all messages from these source_session_ids to completely wipe the batch
    all_messages_to_delete = session.query(TriadBlock).filter(TriadBlock.source_session_id.in_(list(source_sessions))).all()
    all_message_ids = [m.id for m in all_messages_to_delete]
    
    print(f"Deleting {len(all_message_ids)} total messages (and their memories) to ensure clean re-extraction.")
    
    # 4. Delete memories and registry
    memory_ids_to_delete = []
    for table_cls in [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]:
        mems = session.query(table_cls.id).filter(table_cls.message_id.in_(all_message_ids)).all()
        m_ids = [m[0] for m in mems]
        memory_ids_to_delete.extend(m_ids)
        if m_ids:
            session.query(table_cls).filter(table_cls.id.in_(m_ids)).delete(synchronize_session=False)

    if memory_ids_to_delete:
        print(f"Deleting {len(memory_ids_to_delete)} total memories from the global registry.")
        session.query(MemoryRegistry).filter(MemoryRegistry.id.in_(memory_ids_to_delete)).delete(synchronize_session=False)

    # 5. Delete the messages
    session.query(TriadBlock).filter(TriadBlock.id.in_(all_message_ids)).delete(synchronize_session=False)

    # 6. Find the corresponding jobs and set them to pending
    jobs = session.query(ProcessingJob).all()
    requeued = 0
    for job in jobs:
        if not job.raw_response:
            continue
        try:
            meta = json.loads(job.raw_response)
            if not meta: continue
            if isinstance(meta[0], str): # Old format
                job_session_ids = meta
            else:
                job_session_ids = [m["id"] for m in meta]
                
            job_session_id_str = json.dumps(job_session_ids)
            if job_session_id_str in source_sessions:
                job.status = 'pending'
                job.retry_count = 0
                requeued += 1
        except Exception:
            pass

    print(f"Re-queued {requeued} jobs out of {len(source_sessions)} distinct batched session sets.")

    session.commit()
    print("Done. Run `python eval/ingest_api.py run` again.")

if __name__ == "__main__":
    recover()
