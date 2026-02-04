import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from Database.db_setup import engine, init_db, ProcessingJob, Topic, TriadBlock, Memory

class DatabaseManager:
    def __init__(self):
        init_db()
        self.Session = sessionmaker(bind=engine)

    def _to_blob(self, vector):
        """Convert numpy array to bytes for storage"""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        if hasattr(vector, 'detach'): 
            vector = vector.detach().cpu().numpy()
        return vector.astype(np.float32).tobytes()

    def _from_blob(self, blob):
        """Convert bytes back to numpy array"""
        return np.frombuffer(blob, dtype=np.float32)

    def add_to_queue(self, prompt, response, next_prompt):
        session = self.Session()
        try:
            new_job = ProcessingJob(
                raw_prompt=prompt,
                raw_response=response,
                raw_next_prompt=next_prompt
            )
            session.add(new_job)
            session.commit()
            print("[DB] Added job to queue.")
        except Exception as e:
            session.rollback()
            print(f"[DB Error] Add Queue: {e}")
        finally:
            session.close()

    def get_pending_job(self, max_retries=3):
        session = self.Session()
        try:
            job = session.query(ProcessingJob)\
                .filter(ProcessingJob.status.in_(['pending', 'failed']))\
                .filter(ProcessingJob.retry_count < max_retries)\
                .order_by(ProcessingJob.timestamp.asc())\
                .first()

            if job:
                job.status = 'processing'
                session.commit()
                return {
                    'id': job.id,
                    'raw_prompt': job.raw_prompt,
                    'raw_response': job.raw_response,
                    'raw_next_prompt': job.raw_next_prompt
                }
            return None
        finally:
            session.close()

    def mark_job_status(self, job_id, status):
        session = self.Session()
        try:
            job = session.query(ProcessingJob).get(job_id)
            if job:
                job.status = status
                if status == 'failed':
                    job.retry_count += 1
                session.commit()
        finally:
            session.close()
            
    def _get_or_create_topic_path(self, session, chain):
        """
        Takes a list like ['MindCache', 'Backend', 'Database']
        Walks the tree. Creates missing nodes. Returns the Leaf Topic.
        """
        if not chain:
            chain = ["General"] # Fallback

        parent_node = None
        current_node = None

        for level, name in enumerate(chain):
            query = session.query(Topic).filter(func.lower(Topic.name) == name.lower())
            
            if parent_node:
                query = query.filter(Topic.parent_id == parent_node.id)
            else:
                query = query.filter(Topic.parent_id.is_(None))
            
            current_node = query.first()

            if not current_node:
                current_node = Topic(
                    name=name,
                    level=level,
                    parent=parent_node # Sets parent_id automatically
                )
                session.add(current_node)
                session.flush() # CRITICAL: Get ID immediately for next loop
            
            parent_node = current_node 
        return current_node

    def save_extracted_memory(self, job_id, raw_msg, extracted_data):
        session = self.Session()
        try:
            topics_root = extracted_data.get("topics_root", [])
            memory_buckets = extracted_data.get("memory", [])
            summary = extracted_data.get("summary", "")
            
            if not memory_buckets:
                print(f"[DB] Job {job_id} discarded")
                session.execute(text("DELETE FROM processing_queue WHERE id=:id"), {"id": job_id})
                session.commit()
                return

            new_message = TriadBlock(
                summary=summary, 
                timestamp=datetime.now(),
                raw_text=raw_msg
            )
            session.add(new_message)
            session.flush()
            for bucket in memory_buckets:
                topics_branch = bucket.get("topics_branch", [])
                full_chain = topics_root + topics_branch
                
                topic_leaf_node = self._get_or_create_topic_path(session, full_chain)

                for m_type in ["user", "fact", "epis"]:
                    texts = bucket.get(m_type, [])
                    if not texts:
                        continue
                    
                    for text in texts:
                        atom = Memory(
                            content=text,
                            type=m_type,
                            topic=topic_leaf_node,          # Link to Graph Node
                            message=new_message # Link to Time Node
                        )
                        session.add(atom)

            job = session.query(ProcessingJob).get(job_id)
            if job:
                session.delete(job)

            session.commit()
            print(f"[DB] Success! Job {job_id} ")

        except Exception as e:
            session.rollback()
            print(f"[DB Error] Save Memory: {e}")
            self.mark_job_status(job_id, "failed")
        finally:
            session.close()