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
            
    _embedder = None

    @classmethod
    def _get_embedder(cls):
        """Load embedding model once, reuse across all saves."""
        if cls._embedder is None:
            from Database.embedder import EmbeddingManager
            cls._embedder = EmbeddingManager()
        return cls._embedder

    def _semantic_match(self, name, siblings):
        """
        Find the best semantic match for `name` among existing `siblings`.
        Returns the matching Topic if similarity > threshold, else None.
        """
        SIMILARITY_THRESHOLD = 0.99
        if not siblings:
            return None

        embedder = self._get_embedder()
        sibling_names = [s.name for s in siblings]
        
        # Embed new name + all sibling names in one batch
        all_texts = [name] + sibling_names
        vectors = embedder.get_batch_embeddings(all_texts)
        
        new_vec = vectors[0]
        sibling_vecs = vectors[1:]
        
        # Cosine similarity (vectors are already normalized)
        similarities = np.dot(sibling_vecs, new_vec)
        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]

        if best_score >= SIMILARITY_THRESHOLD:
            matched = siblings[best_idx]
            print(f"[TopicNorm] Merged '{name}' → '{matched.name}' (sim: {best_score:.2f})")
            return matched
        return None

    def _get_or_create_topic_path(self, session, chain, job_timestamp=None):
        """
        Takes a list like ['MindCache', 'Backend', 'Database']
        Walks the tree. Creates missing nodes. Returns the Leaf Topic.
        
        At each level:
          1. Exact match (case-insensitive) → reuse
          2. Semantic match (cosine sim > 0.75) → reuse existing sibling
          3. No match → create new node
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

            # Step 2: Semantic fallback — check siblings for near-synonyms
            if not current_node:
                if parent_node:
                    siblings = session.query(Topic).filter(Topic.parent_id == parent_node.id).all()
                else:
                    siblings = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
                
                current_node = self._semantic_match(name, siblings)

            # Step 3: Create new node if no match found
            if not current_node:
                current_node = Topic(
                    name=name,
                    level=level,
                    parent=parent_node,
                    timestamp=job_timestamp if job_timestamp else datetime.now()
                )
                session.add(current_node)
                session.flush()
            
            parent_node = current_node 
        return current_node

    def get_topic_tree_hints(self, max_depth=2):
        """
        Returns a formatted string of existing topic paths for prompt grounding.
        Lightweight: just queries root + first two levels.
        """
        session = self.Session()
        try:
            roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
            if not roots:
                return ""
            
            lines = []
            for root in roots:
                lines.append(root.name)
                for child in root.children:
                    lines.append(f"  {child.name}")
                    if max_depth > 1:
                        for grandchild in child.children:
                            lines.append(f"    {grandchild.name}")
            
            return "\n".join(lines)
        finally:
            session.close()

    def save_extracted_memory(self, job_id, raw_msg, extracted_data, source_session_id=None, session_timestamp=None):
        session = self.Session()
        try:
            # Timestamp priority: session_timestamp (dataset) > job.timestamp > now()
            job = session.query(ProcessingJob).get(job_id)
            if session_timestamp:
                from dateutil.parser import parse as parse_dt
                try:
                    ts = parse_dt(session_timestamp) if isinstance(session_timestamp, str) else session_timestamp
                except Exception:
                    ts = datetime.now()
            else:
                ts = job.timestamp if job else datetime.now()

            topics_root = extracted_data.get("topics_root", [])
            memory_buckets = extracted_data.get("memory", [])
            
            if not memory_buckets:
                print(f"[DB] Job {job_id} discarded")
                session.execute(text("DELETE FROM processing_queue WHERE id=:id"), {"id": job_id})
                session.commit()
                return
            
            new_message = TriadBlock(
                raw_msg=raw_msg, 
                timestamp=ts,
                source_session_id=source_session_id
            )
            session.add(new_message)
            session.flush()
            for bucket in memory_buckets:
                topics_branch = bucket.get("topics_branch", [])
                full_chain = topics_root + topics_branch
                # Validate chain is not empty
                if not full_chain:
                    full_chain = ["General"]

                topic_leaf_node = self._get_or_create_topic_path(session, full_chain, job_timestamp=ts)

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

                        if MemoryClass == DecisionMemory:
                            atom = MemoryClass(
                                id=reg.id,
                                content=text,
                                topic=topic_leaf_node,
                                message=new_message,
                                timestamp=ts,
                                last_validated_at=ts
                            )
                        else:
                            atom = MemoryClass(
                                id=reg.id,
                                content=text,
                                topic=topic_leaf_node,
                                message=new_message,
                                timestamp=ts
                            )
                        session.add(atom)

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

    def run_decision_state_analyzer(self):
        """
        Background job: finds decisions that haven't been analyzed yet
        (no context or no last_validated_at) and runs the analyzer per topic.
        Can be called anytime — fully decoupled from save_extracted_memory.
        """
        session = self.Session()
        try:
            unanalyzed = (
                session.query(DecisionMemory.topic_id)
                .filter(
                    (DecisionMemory.context.is_(None)) | 
                    (DecisionMemory.last_validated_at.is_(None))
                )
                .distinct()
                .all()
            )
            
            topic_ids = [row[0] for row in unanalyzed if row[0] is not None]
            
            if not topic_ids:
                print("[DecisionAnalyzer] No unanalyzed decisions found.")
                return
            
            print(f"[DecisionAnalyzer] Found {len(topic_ids)} topic(s) with unanalyzed decisions.")
            analyzer = DecisionStateAnalyzer()
            
            for tid in topic_ids:
                analyzer.analyze(session, tid)
            
            session.commit()
            print(f"[DecisionAnalyzer] Complete.")
            
        except Exception as e:
            session.rollback()
            print(f"[DB Error] Decision Analyzer: {e}")
        finally:
            session.close()

