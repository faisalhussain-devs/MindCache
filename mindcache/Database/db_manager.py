from sqlalchemy.ext.asyncio import session
import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, text
from mindcache.Database.db_setup import init_db, ProcessingJob, Topic, TriadBlock, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory, MemoryRegistry, to_numpy, Session
from mindcache.Database.decision_analyzer import DecisionStateAnalyzer

import re
import logging
logger = logging.getLogger(__name__)

def normalize_name(name: str) -> str:
    """Normalize a topic name: trim whitespace, lowercase, collapse spacing."""
    if not name:
        return ""
    n = name.lower().strip()
    if n.startswith("general "):
        n = n[len("general "):]
    n = re.sub(r'\s+', ' ', n)
    if n.endswith("ies"):
        n = n[:-3] + "y"
    elif n.endswith("s") and not n.endswith("ss"):
        n = n[:-1]
    return n

class TopicResolver:
    """
    Handles case-insensitive resolving of topic paths (like ['Computer Science', 'Python']).
    Resolves the entire chain with a single DB query to prevent N database roundtrips.
    """
    def __init__(self, session, chain, user_id="default", use_cache=False):
        self.session = session
        self.user_id = user_id
        self.cache = {}  # (parent_id, normalized_name) -> Topic
        
        # 1. Normalize all names in the chain
        self.normalized_names = [normalize_name(name) for name in chain]
        
        # 2. Query all existing topics matching these normalized names in ONE query
        if self.normalized_names:
            existing_topics = self.session.query(Topic).filter(
                Topic.user_id == self.user_id,
                Topic.name_normalized.in_(self.normalized_names)
            ).all()
            
            for t in existing_topics:
                # Cache them using their parent_id and normalized_name
                normalized = t.name_normalized or normalize_name(t.name)
                self.cache[(t.parent_id, normalized)] = t

    def resolve(self, parent_id, name, job_timestamp=None, level=0):
        normalized = normalize_name(name)
        
        # Check in-memory single-query cache first
        node = self.cache.get((parent_id, normalized))
        if node:
            return node
        
        # Not found -> create new topic node on the fly
        node = Topic(
            name=name,
            name_normalized=normalized,
            level=level,
            parent_id=parent_id,
            user_id=self.user_id,
            timestamp=job_timestamp or datetime.now()
        )
        self.session.add(node)
        self.session.flush()
        
        # Add to local cache so child nodes at the next level can resolve it
        self.cache[(parent_id, normalized)] = node
        return node

class DatabaseManager:
    def __init__(self):
        init_db()
        self.Session = Session

    @staticmethod
    def _to_blob(vector):
        """Convert numpy array to bytes for storage"""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        if hasattr(vector, 'detach'): 
            vector = vector.detach().cpu().numpy()
        return vector.astype(np.float32).tobytes()

    @staticmethod
    def _from_blob(blob):
        """Convert bytes back to numpy array"""
        return to_numpy(blob)

    def add_to_queue(self, prompt, turn_ids=None, timestamp=None, user_id="default"):
        session = self.Session()
        try:
            new_job = ProcessingJob(
                raw_prompt=prompt,
                turn_ids=turn_ids,
                timestamp=timestamp,
                user_id=user_id
            )
            session.add(new_job)
            session.commit()
            logger.info("[DB] Added job to queue.")
        except Exception as e:
            session.rollback()
            logger.error(f"[DB Error] add_to_queue: {e}")
        finally:
            session.close()

    def get_pending_job(self, max_retries=33):
        session = self.Session()
        try:
            job = session.query(ProcessingJob)\
                .filter(ProcessingJob.status.in_(["processing", "pending", "failed"]))\
                .filter(ProcessingJob.retry_count < max_retries)\
                .order_by(ProcessingJob.timestamp.asc())\
                .first()

            if job:
                job.status = 'processing'
                session.commit()
                return {
                    'id': job.id,
                    'raw_prompt': job.raw_prompt,
                    'turn_ids': job.turn_ids
                }
            return None
        finally:
            session.close()

    def mark_job_status(self, job_id, status):
        session = self.Session()
        try:
            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = status
                if status == 'failed':
                    job.retry_count += 1
                session.commit()
        finally:
            session.close()
            
    @classmethod
    def _get_embedder(cls):
        """Load embedding model once, reuse across all saves."""
        from mindcache.Database.embedder import EmbeddingManager
        return EmbeddingManager()

    def _get_or_create_topic_path(self, session, chain, job_timestamp=None, user_id="default", resolver=None):
        """
        Takes a list like ['MindCache', 'Backend', 'Database']
        Walks the tree. Creates missing nodes. Returns the Leaf Topic.
        """
        if not chain:
            chain = ["General"] # Fallback

        if resolver is None:
            resolver = TopicResolver(session, chain, user_id=user_id)

        parent_node = None
        current_node = None

        for level, name in enumerate(chain):
            parent_id = parent_node.id if parent_node else None
            current_node = resolver.resolve(parent_id, name, job_timestamp=job_timestamp, level=level)
            parent_node = current_node
            
        # Step 3: Enforce Leaf Node Constraint
        if current_node.children:
            generic_name = f"General {current_node.name}"
            parent_id = current_node.id
            leaf_node = resolver.resolve(parent_id, generic_name, job_timestamp=job_timestamp, level=current_node.level + 1)
            return leaf_node
            
        return current_node

    _TIMESTAMP_RE = None

    @classmethod
    def _strip_timestamps(cls, text: str) -> str:
        """Remove timestamp lines/headers so they don't pollute the embedding."""
        import re
        if cls._TIMESTAMP_RE is None:
            cls._TIMESTAMP_RE = re.compile(
                r"(?m)^Timestamp:.*$|"              # "Timestamp: March-01-2024"
                r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?\b|"  # ISO datetime
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-\s]\d{1,2}[-,\s]\d{4}\b",
                re.IGNORECASE,
            )
        return cls._TIMESTAMP_RE.sub("", text).strip()


    @staticmethod
    def _build_leaf_embed_text(leaf, parent_map, char_budget=8000) -> str:
        """
        Build embedding text for a leaf: full path + newest memories first
        until the ~8000 char budget (~2000 tokens) is used up.
        """
        # Walk up to build path
        curr = leaf
        path_parts = []
        while curr:
            path_parts.append(curr.name)
            curr = parent_map.get(curr.id)
        path_str = "Path: " + " > ".join(reversed(path_parts))

        budget = char_budget - len(path_str)
        mem_lines = []

        # Gather all memories
        all_mems = (
            list(leaf.episodic_memories) +
            list(leaf.knowledge_memories) +
            list(leaf.user_memories) +
            list(leaf.decision_memories)
        )
        # Sort combined list newest-first
        all_mems.sort(key=lambda m: m.timestamp, reverse=True)

        for mem in all_mems:
            if budget <= 0:
                break
            content = (mem.content or "").strip()
            if not content:
                continue
            mem_lines.append(content)
            budget -= len(content) + 1  # +1 for separator

        if mem_lines:
            return path_str + "\n" + "\n".join(mem_lines)
        return path_str

    def get_top_leaf_paths(self, raw_text: str, top_k: int = 5, query_embedding: bytes = None) -> list[str]:
        """
        Smart Ingestion: embed the incoming raw job text, compare against all
        leaf embeddings (Path + newest memories up to 8000 Nomic tokens), return
        the top_k full path strings for LLM grounding.

        Also stores the job embedding back on the ProcessingJob row if the job
        is passed in — but here we just return paths for use in the prompt.
        """
        embedder = self._get_embedder()
        session = self.Session()
        try:
            # 1. Embed the query / partitions
            if query_embedding is not None:
                # Reconstruct the N x 768 matrix of partition vectors
                query_matrix = to_numpy(query_embedding).reshape(-1, 768)
            else:
                clean_text = self._strip_timestamps(raw_text)
                if not clean_text:
                    return []
                query_matrix = embedder.encode([clean_text], is_query=False)  # shape (1, dim)

            # 2. Load all topics, build helper maps
            topics = session.query(Topic).all()
            if not topics:
                return []

            topic_by_id = {t.id: t for t in topics}
            parent_map  = {t.id: topic_by_id.get(t.parent_id) for t in topics}
            parent_ids  = {t.parent_id for t in topics if t.parent_id is not None}
            leaves      = [t for t in topics if t.id not in parent_ids]

            if not leaves:
                return []

            # 3. Build / refresh missing or stale leaf embeddings.
            #
            # Two cases where an embedding needs to be (re)built:
            #   a) leaf.embedding is None  — never embedded yet.
            #   b) leaf has NO description AND enough new memories have arrived since
            #      the last embed (>= STALE_EMBED_THRESHOLD of total).
            #
            # When a description EXISTS, the summarizer already sets embedding=None
            # whenever it updates the description, so staleness is handled there
            # automatically — no need to count memories here.
            STALE_EMBED_THRESHOLD = 0.25  # 10% new memories → re-embed (description-less leaves only)

            to_embed = []
            leaf_vecs = []
            for leaf in leaves:
                needs_embed = leaf.embedding is None

                if not needs_embed and not (leaf.description or "").strip():
                    # No description — embedding was built from raw memories.
                    # Check staleness: how many memories arrived after the embed anchor?
                    if leaf.timestamp is not None:
                        embed_ts = leaf.timestamp
                        all_mems = (
                            list(leaf.episodic_memories) +
                            list(leaf.knowledge_memories) +
                            list(leaf.user_memories) +
                            list(leaf.decision_memories)
                        )
                        total_count = len(all_mems)
                        if total_count > 0:
                            new_count = sum(
                                1 for m in all_mems
                                if m.timestamp is not None and m.timestamp > embed_ts
                            )
                            if new_count / total_count >= STALE_EMBED_THRESHOLD:
                                logger.info(f"[Embed] Leaf '{leaf.name}' is stale "
                                      f"({new_count}/{total_count} new memories since embed). Re-embedding.")
                                needs_embed = True

                if needs_embed:
                    # Use description if available, otherwise build from raw memories
                    desc = (leaf.description or "").strip()
                    embed_text = desc if desc else self._build_leaf_embed_text(leaf, parent_map)
                    to_embed.append((leaf, embed_text))
                else:
                    leaf_vecs.append((leaf.id, self._from_blob(leaf.embedding)))

            if to_embed:
                batch_size = 16
                for i in range(0, len(to_embed), batch_size):
                    batch = to_embed[i : i + batch_size]
                    texts = [b[1] for b in batch]
                    vecs  = embedder.get_batch_embeddings(texts)
                    for j, (leaf, _) in enumerate(batch):
                        blob = embedder._to_blob(vecs[j])
                        leaf.embedding = blob
                        leaf.timestamp = datetime.now()  # anchor for staleness checks
                        leaf_vecs.append((leaf.id, vecs[j]))
                session.commit()

            # Sort leaf_vecs to align with leaves list
            vec_map = dict(leaf_vecs)
            ordered_vecs = [vec_map[leaf.id] for leaf in leaves]
            leaf_matrix = np.vstack(ordered_vecs)         # (N_leaves, dim)

            # 4. Multi-vector Cosine similarity
            # query_matrix shape: (N_partitions, dim)
            # leaf_matrix shape: (N_leaves, dim)
            sims = np.dot(query_matrix, leaf_matrix.T)    # (N_partitions, N_leaves)

            # 5. Top-K Selection per partition & Union
            top_paths_set = []
            for i in range(sims.shape[0]):
                partition_sims = sims[i]
                # Select top 5 for each partition vector
                top_indices = np.argsort(partition_sims)[::-1][:top_k]
                for idx in top_indices:
                    leaf = leaves[idx]
                    curr = leaf
                    parts = []
                    while curr:
                        parts.append(curr.name)
                        curr = parent_map.get(curr.id)
                    parts.reverse()
                    path = " > ".join(parts)
                    if path not in top_paths_set:
                        top_paths_set.append(path)

            return top_paths_set[:top_k]

        finally:
            session.close()

    def save_extracted_memory(self, job_id, raw_msg, extracted_data, source_session_id=None, session_timestamp=None, user_id="default"):
        session = self.Session()
        try:
            # Timestamp priority: session_timestamp (dataset) > job.timestamp > now()
            job = session.get(ProcessingJob, job_id)
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
                logger.info(f"[DB] Job {job_id} discarded (no memory buckets).")
                session.execute(text("DELETE FROM processing_queue WHERE id=:id"), {"id": job_id})
                session.commit()
                return
            
            new_message = TriadBlock(
                raw_msg=raw_msg,
                timestamp=ts,
                source_session_id=source_session_id,
                user_id=user_id
            )
            session.add(new_message)
            session.flush()

            # ── Deduplication: build global hash sets ONCE before the bucket loop ──
            import hashlib

            def _h(text: str) -> str:
                return hashlib.sha256(text.lower().strip().encode()).hexdigest()

            # Pre-load all existing memory hashes globally filtered by user_id
            global_hashes = {
                UserMemory: { _h(c) for (c,) in session.query(UserMemory.content).filter(UserMemory.user_id == user_id).all() if c },
                KnowledgeMemory: { _h(c) for (c,) in session.query(KnowledgeMemory.content).filter(KnowledgeMemory.user_id == user_id).all() if c },
                EpisodicMemory: { _h(c) for (c,) in session.query(EpisodicMemory.content).filter(EpisodicMemory.user_id == user_id).all() if c },
                DecisionMemory: { _h(c) for (c,) in session.query(DecisionMemory.content).filter(DecisionMemory.user_id == user_id).all() if c }
            }
            
            # Preload all topic names across all branches/root in a single query
            all_chain_names = list(topics_root)
            for bucket in memory_buckets:
                all_chain_names.extend(bucket.get("topics_branch", []))
            resolver = TopicResolver(session, all_chain_names, user_id=user_id)
            # ───────────────────────────────────────────────────────────────────────

            for bucket in memory_buckets:
                topics_branch = bucket.get("topics_branch", [])
                full_chain = topics_root + topics_branch
                if not full_chain:
                    full_chain = ["General"]

                topic_leaf_node = self._get_or_create_topic_path(
                    session, full_chain, job_timestamp=ts, user_id=user_id, resolver=resolver
                )

                # Map JSON schema bucket keys to (MemoryClass, registry_type)
                type_map = {
                    "user":     (UserMemory,     "user"),
                    "fact":     (KnowledgeMemory, "knowledge"),
                    "epis":     (EpisodicMemory,  "episodic"),
                    "decision": (DecisionMemory,  "decision"),
                }

                for m_type, (MemoryClass, registry_type) in type_map.items():
                    texts = bucket.get(m_type, [])
                    if not texts:
                        continue

                    for mem_text in texts:
                        mem_hash = _h(mem_text)

                        # ── Global O(1) dedup check ───────────────────────
                        if mem_hash in global_hashes[MemoryClass]:
                            logger.info(f"[DB] Dedup: skipped exact-duplicate {registry_type}: {mem_text[:60]}...")
                            continue
                        # ── End dedup ─────────────────────────────────────

                        # Register in global registry first
                        reg = MemoryRegistry(memory_type=registry_type, user_id=user_id)
                        session.add(reg)
                        session.flush()  # Get the global ID

                        if MemoryClass == DecisionMemory:
                            atom = MemoryClass(
                                id=reg.id,
                                content=mem_text,
                                topic=topic_leaf_node,
                                message=new_message,
                                timestamp=ts,
                                last_validated_at=ts,
                                user_id=user_id
                            )
                        else:
                            atom = MemoryClass(
                                id=reg.id,
                                content=mem_text,
                                topic=topic_leaf_node,
                                message=new_message,
                                timestamp=ts,
                                user_id=user_id
                            )
                        session.add(atom)

                        # Update global hash set so duplicates in the current batch are caught
                        global_hashes[MemoryClass].add(mem_hash)

                    topic_leaf_node.embedding = None

            if job:
                session.delete(job)

            session.commit()
            logger.info(f"[DB] Success! Job {job_id} ")
        except Exception as e:
            session.rollback()
            logger.error(f"[DB Error] Save Memory: {e}")
            self.mark_job_status(job_id, "failed")
        finally:
            session.close()

    def run_decision_state_analyzer(self, user_id="default"):
        """
        Background job: finds decisions that haven't been analyzed yet
        (no context or no last_validated_at) and runs the analyzer per topic.
        Can be called anytime — fully decoupled from save_extracted_memory.
        """
        session = self.Session()
        try:
            from sqlalchemy import select
            multiple_decisions_subquery = (
                session.query(DecisionMemory.topic_id)
                .filter(DecisionMemory.user_id == user_id)
                .group_by(DecisionMemory.topic_id)
                .having(func.count(DecisionMemory.id) > 1)
                .subquery()
            )
            # 2. Main query to find topic_ids with unanalyzed decisions within those topics
            unanalyzed = (
                session.query(DecisionMemory.topic_id)
                .filter(
                    DecisionMemory.user_id == user_id
                )
                .filter(
                    (DecisionMemory.context.is_(None)) | 
                    (DecisionMemory.last_validated_at.is_(None))
                )
                .filter(DecisionMemory.topic_id.in_(select(multiple_decisions_subquery.c.topic_id)))
                .distinct()
                .all()
            )
            logger.info(f"[DB] There are {len(unanalyzed)} decisions that need to be analyzed for user '{user_id}'.")
            topic_ids = [row[0] for row in unanalyzed if row[0] is not None]
            
            if not topic_ids:
                logger.info(f"[DecisionAnalyzer] No unanalyzed decisions found for user '{user_id}'.")
                return
            
            logger.info(f"[DecisionAnalyzer] Found {len(topic_ids)} topic(s) with unanalyzed decisions.")
            analyzer = DecisionStateAnalyzer()
            
            for tid in topic_ids:
                analyzer.analyze(session, tid, user_id=user_id)
            
            session.commit()
            logger.info(f"[DecisionAnalyzer] Complete.")
            
        except Exception as e:
            session.rollback()
            logger.error(f"[DB Error] Decision Analyzer: {e}")
        finally:
            session.close()

    def process_memory(self):
        from mindcache.Memory_extract.input_denoiser import InputDenoiser
        from mindcache.Memory_extract.memory_extractor import Memory_Extractor
        inp_denoiser = InputDenoiser()
        mem_ext = Memory_Extractor()
        
        while True:
            job = self.get_pending_job()
            if not job:
                logger.info("No more jobs in queue. Worker going to sleep.")
                break
            job_id, prompt = job["id"], job["raw_prompt"]
            compressed_input = inp_denoiser.compress(prompt)
            logger.info(f"\n[Worker] Processing Job #{job_id}...")
        
            extracted_data = mem_ext.memory_extract(compressed_input)     

            self.save_extracted_memory(
                job_id,
                compressed_input, 
                extracted_data
            )
if __name__ == "__main__":
    db_manager = DatabaseManager()
    db_manager.process_memory() 