import numpy as np
import json
import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, func
from Database.db_setup import engine, init_db, ProcessingJob, Topic, TriadBlock, Memory

class DatabaseManager:
    def __init__(self):
        # Initialize tables if they don't exist
        init_db()
        # Create the Session Factory
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

    # QUEUE OPERATIONS
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
                # Return a dict so we can close the session safely
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

    # HELPER: TOPIC TREE TRAVERSAL
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
            # 1. Search for node at this level
            query = session.query(Topic).filter(func.lower(Topic.name) == name.lower())
            
            if parent_node:
                # Must be a child of the previous node
                query = query.filter(Topic.parent_id == parent_node.id)
            else:
                # Must be a root node (no parent)
                query = query.filter(Topic.parent_id.is_(None))
            
            current_node = query.first()

            # 2. Create if missing
            if not current_node:
                current_node = Topic(
                    name=name,
                    level=level,
                    parent=parent_node # Sets parent_id automatically
                )
                session.add(current_node)
                session.flush() # CRITICAL: Get ID immediately for next loop
            
            # 3. Step down
            parent_node = current_node
            
        return current_node


    # SAVE EXTRACTED MEMORY
    def save_extracted_memory(self, job_id, raw_msg, extracted_data, content_vec_map):
        session = self.Session()
        try:
            # 1. Parse Input Data
            topics_root = extracted_data.get("topics_root", [])
            memory_buckets = extracted_data.get("memories", [])
            summary = extracted_data.get("summary", "")
            
            # Validation: If no memories, just cleanup
            if not memory_buckets:
                print(f"[DB] Job {job_id} discarded (No memory buckets found).")
                session.execute(text("DELETE FROM processing_queue WHERE id=:id"), {"id": job_id})
                session.commit()
                return

            # 2. Create Time Anchor (The "TriadBlock")
            # This represents the chat message event itself
            new_message = TriadBlock(
                summary=summary, 
                timestamp=datetime.now(),
                raw_text=raw_msg
            )
            session.add(new_message)
            session.flush()

            # 4. Loop through the "Buckets" (Branches)
            for i, bucket in enumerate(memory_buckets):
                # A. Resolve Full Path
                topics_branch = bucket.get("topics_branch", [])
                full_chain = topics_root + topics_branch
                
                # B. Get the Graph Node (Leaf)
                topic_leaf_node = self._get_or_create_topic_path(session, full_chain)

                # C. Process each memory type inside this bucket
                for m_type in ["user", "fact", "epis"]:
                    texts = bucket.get(m_type, [])
                    if not texts:
                        continue
                    
                    # Get the Big List of Vectors for this type
                    all_vectors = content_vec_map[i].get(m_type, [])
                    
                    for current_idx, text in enumerate(texts):
                        if current_idx < len(all_vectors):
                            vec = all_vectors[current_idx]
                            blob = self._to_blob(vec)
                            
                            atom = Memory(
                                content=text,
                                type=m_type,
                                embedding=blob,
                                topic=topic_leaf_node,          # Link to Graph Node
                                source_message=new_message # Link to Time Node
                            )
                            session.add(atom)
                            
                        else:
                            print(f"[DB Warning] Missing vector for text: {text[:30]}...")

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

    # SEARCH OPERATIONS
    def search(self, query_topic_name, query_vec, limit=5):
        """
        Hybrid Search:
        1. Filter by Topic Name (Graph Traversal) - FAST
        2. Rank by Vector Similarity (Vector Search) - ACCURATE
        """
        session = self.Session()
        results = []
        try:
            # Step 1: Filter Logic (Graph)
            # Find the target topic and its children
            target_topic = session.query(Topic).filter_by(name=query_topic_name).first()
            
            if not target_topic:
                print(f"[DB] Topic '{query_topic_name}' not found. Searching all.")
                # Fallback: Search everything if topic not found
                candidate_memories = session.query(Memory).all()
            else:
                # Get memories from this topic
                candidate_memories = target_topic.memories
                # OPTIONAL: Get memories from children too (Recursive)
                for child in target_topic.children:
                    candidate_memories.extend(child.memories)

            # Step 2: Vector Rank Logic
            # Note: Doing dot product in Python is fine for <10k items. 
            # For >10k, use sqlite-vec or FAISS.
            q_vec = self._from_blob(self._to_blob(query_vec))
            
            scored_results = []
            for mem in candidate_memories:
                mem_vec = self._from_blob(mem.embedding)
                score = np.dot(mem_vec, q_vec)
                scored_results.append((score, mem))

            # Step 3: Sort and Return
            scored_results.sort(key=lambda x: x[0], reverse=True)
            
            for score, mem in scored_results[:limit]:
                results.append({
                    "content": mem.content,
                    "type": mem.type,
                    "score": float(score),
                    "topic": mem.topic.name,
                    "source_id": mem.message_id
                })

        finally:
            session.close()
        
        return results