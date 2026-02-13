import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from Database.db_setup import engine, init_db, ProcessingJob, Topic, TriadBlock, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory, MemoryRegistry
from Database.decision_analyzer import DecisionStateAnalyzer

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
        decision_topic_ids = set()  # Track topics that received new decisions
        try:
            topics_root = extracted_data.get("topics_root", [])
            memory_buckets = extracted_data.get("memory", [])
            
            if not memory_buckets:
                print(f"[DB] Job {job_id} discarded")
                session.execute(text("DELETE FROM processing_queue WHERE id=:id"), {"id": job_id})
                session.commit()
                return

            new_message = TriadBlock(
                raw_msg=raw_msg, 
                timestamp=datetime.now()
            )
            session.add(new_message)
            session.flush()
            for bucket in memory_buckets:
                topics_branch = bucket.get("topics_branch", [])
                full_chain = topics_root + topics_branch
                
                topic_leaf_node = self._get_or_create_topic_path(session, full_chain)

                # Map bucket keys to (MemoryClass, registry_type)
                type_map = {
                    "user": (UserMemory, "user"),
                    "fact": (KnowledgeMemory, "knowledge"),
                    "episodic": (EpisodicMemory, "episodic"),
                    "decision": (DecisionMemory, "decision"),
                }

                for m_type, (MemoryClass, registry_type) in type_map.items():
                    texts = bucket.get(m_type, [])
                    if not texts:
                        continue
                    
                    for text in texts:
                        # Register in global registry first
                        reg = MemoryRegistry(memory_type=registry_type)
                        session.add(reg)
                        session.flush()  # Get the global ID

                        atom = MemoryClass(
                            id=reg.id,
                            content=text,
                            topic=topic_leaf_node,
                            message=new_message
                        )
                        session.add(atom)

                        # Track if this was a decision
                        if m_type == "decision":
                            decision_topic_ids.add(topic_leaf_node.id)

            job = session.query(ProcessingJob).get(job_id)
            if job:
                session.delete(job)

            session.commit()
            print(f"[DB] Success! Job {job_id} ")

            # --- CHAIN: Decision State Analyzer ---
            if decision_topic_ids:
                print(f"[DB] New decisions detected. Running Decision Analyzer for {len(decision_topic_ids)} topic(s)...")
                analyzer = DecisionStateAnalyzer()
                analysis_session = self.Session()
                try:
                    for tid in decision_topic_ids:
                        analyzer.analyze(analysis_session, tid)
                    analysis_session.commit()
                    print(f"[DB] Decision analysis complete.")
                except Exception as e:
                    analysis_session.rollback()
                    print(f"[DB Error] Decision Analyzer: {e}")
                finally:
                    analysis_session.close()

        except Exception as e:
            session.rollback()
            print(f"[DB Error] Save Memory: {e}")
            self.mark_job_status(job_id, "failed")
        finally:
            session.close()