import os
from datetime import datetime

from mindcache.Database.db_setup import init_db, ProcessingJob, MemoryRegistry, Topic, UserMemory, KnowledgeMemory, EpisodicMemory, DecisionMemory, Session
from mindcache.exceptions import IngestionError, RetrievalError
from mindcache.Database.db_manager import DatabaseManager
from mindcache.retrieval.active_path import ActivePathRetrieval
from mindcache.Memory_extract.memory_extractor import Memory_Extractor
import logging
logger = logging.getLogger(__name__)
_REORG_THRESHOLD = 60
_PROCESS_LIMIT = 30
_MAX_RETRIES = 5
_CONSOLIDATION_TARGET = 4000
_CONSOLIDATION_HARD_LIMIT = 7000

class MindCache:
    """
    MindCache — Structured Long-Term Memory SDK for LLM Agents.
    Provides persistent, hierarchical memory with zero-copy vector search.
    
    Core Verbs API:
        mc = MindCache(db_path="mindcache.db", provider="gemini", model_name="gemini-2.5-flash")
        
        # 1. Queue conversation turns (fast SQLite write in ms)
        mc.add([
            {"role": "user", "content": "I prefer working with Python and FastAPI."},
            {"role": "assistant", "content": "Got it! We will focus on Python and FastAPI."}
        ], user_id="alice")
        
        # 2. Process queued conversations (LLM extraction & memory tree maintenance)
        mc.process(user_id="alice")
        
        # 3. Retrieve relevant context formatted for LLM system prompt
        context = mc.search("What are my preferred tools?", user_id="alice")

        # 4. Inspect remembered state ('memories', 'tree', or 'all')
        state = mc.inspect(user_id="alice", view="memories")

        # 5. Remove a specific memory
        mc.forget(memory_id=12, user_id="alice")

        # 6. Clear all memory state for a user
        mc.reset(user_id="alice")
    """
    def __init__(
        self,
        db_path: str = None,
        gemini_api_key: str = None,
        provider: str = "gemini",
        model_name: str = "gemini-2.5-flash",
        enable_summarization: bool = True,
    ):
        if db_path:
            os.environ["MINDCACHE_DB_PATH"] = db_path
            from mindcache.Database.db_setup import reconfigure_engine
            reconfigure_engine(db_path)
            from mindcache.retrieval.root_cache import refresh_tree_cache
            refresh_tree_cache()
        if gemini_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            os.environ["GEMINI_API_KEYS"] = gemini_api_key
            
        init_db()
        self.db_manager = DatabaseManager()
        self.retriever = ActivePathRetrieval()
        self.extractor = Memory_Extractor(
            db_manager=self.db_manager,
            model_name=model_name,
            provider=provider
        )
        self.Session = Session
        self.enable_summarization = enable_summarization


    def add(self, messages: list[dict], user_id: str = "default", timestamp: datetime = None) -> int:
        """        
        Asynchronously queue a conversation session for ingestion.
        Writes a ProcessingJob to the SQLite database in milliseconds and returns the Job ID.
        """
        import datetime as dt_module
        if isinstance(timestamp, (int, float)):
            timestamp = dt_module.datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            from dateutil.parser import parse as parse_dt
            try:
                timestamp = parse_dt(timestamp)
            except Exception:
                timestamp = dt_module.datetime.now()

        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "").strip()
            lines.append(f"{role}: {content}")
        raw_prompt = "\n".join(lines)

        session = self.Session()
        try:
            new_job = ProcessingJob(
                raw_prompt=raw_prompt,
                status="pending",
                timestamp=timestamp or datetime.now(),
                user_id=user_id
            )
            session.add(new_job)
            session.commit()
            job_id = new_job.id
            logger.info(f"[MindCache] Session queued for user '{user_id}'. Job ID: {job_id}")
            return job_id
        except Exception as e:
            session.rollback()
            raise IngestionError(f"Failed to queue ingestion job: {e}") from e
        finally:
            session.close()

    def process(self, user_id: str = "default", limit: int = _PROCESS_LIMIT, max_retries: int = _MAX_RETRIES,
        consolidation_max_tokens: int = _CONSOLIDATION_TARGET, consolidation_hard_limit_tokens: int = _CONSOLIDATION_HARD_LIMIT) -> dict:
        """
        Run the memory extraction pipeline on queued jobs for a specific user.

        Flow:
          1. Consolidate ALL small pending jobs into larger DB rows (pre-step).
          2. Fetch up to `limit` jobs from the now-consolidated queue.
          3. Embed + extract + save each job and update topic tree summaries.
        """
        # Step 1: DB-level consolidation (runs before limit fetch)
        self.db_manager.consolidate_processing_jobs(
            user_id=user_id,
            max_tokens=consolidation_max_tokens,
            hard_limit_tokens=consolidation_hard_limit_tokens,
        )

        # Step 2: Fetch jobs (after consolidation)
        try:
            session = self.Session()
            try:
                query = session.query(ProcessingJob).filter(
                    ProcessingJob.status.in_(["processing", "pending", "failed"]),
                    ProcessingJob.retry_count < max_retries,
                    ProcessingJob.user_id == user_id
                ).order_by(ProcessingJob.id).all()

                pending_jobs = query[:limit]

                if not pending_jobs:
                    logger.info(f"[MindCache] No pending jobs in the queue for user '{user_id}'.")
                    return {"success": 0, "failed": 0}

                # Pre-compute embeddings for the fetched batch
                from mindcache.Database.embedder import _batch_embed_pending_jobs
                _batch_embed_pending_jobs(pending_jobs, self.db_manager._get_embedder())

                # Refresh session to load embeddings
                session.expire_all()

                # Detach data and close session to avoid transaction locking contention
                jobs_data = []
                for job in pending_jobs:
                    prompt = job.raw_prompt
                    if prompt is not None:
                        jobs_data.append({
                            "id":         job.id,
                            "raw_prompt": job.raw_prompt,
                            "embedding":  job.embedding,
                            "timestamp":  job.timestamp,
                        })
            finally:
                session.close()
        except Exception as e:
            raise IngestionError(f"Failed to retrieve pending jobs from queue: {e}") from e

        from mindcache.Database.db_setup import TriadBlock
        _snap_sess = self.Session()
        try:
            triads_before = _snap_sess.query(TriadBlock).filter(TriadBlock.user_id == user_id).count()
        finally:
            _snap_sess.close()

        # Step 3: Process each job (original per-job logic, unchanged)
        success = 0
        failed = 0
        for job_data in jobs_data:
            job_id = job_data["id"]
            try:
                extracted_data = self.extractor.memory_extract(
                    job_data["raw_prompt"],
                    query_embedding=job_data["embedding"],
                )

                if not extracted_data:
                    sess = self.Session()
                    try:
                        job = sess.query(ProcessingJob).get(job_id)
                        if job:
                            job.status = "failed"
                            job.retry_count += 1
                            sess.commit()
                    finally:
                        sess.close()
                    failed += 1
                    continue

                self.db_manager.save_extracted_memory(
                    job_id=job_id,
                    raw_msg=job_data["raw_prompt"],
                    extracted_data=extracted_data,
                    session_timestamp=job_data["timestamp"],
                    user_id=user_id
                )
                success += 1
            except Exception as e:
                logger.error(f"[MindCache Error] Job {job_id} failed: {e}")
                sess = self.Session()
                try:
                    job = sess.query(ProcessingJob).get(job_id)
                    if job:
                        job.status = "failed"
                        job.retry_count += 1
                        sess.commit()
                finally:
                    sess.close()
                failed += 1

        # Run decision analyzer to update decision statuses
        if success > 0:
            logger.info(f"[MindCache] Running decision state analyzer for user '{user_id}'...")
            try:
                self.db_manager.run_decision_state_analyzer(user_id=user_id)
            except Exception as dec_err:
                logger.warning(f"[MindCache Warning] Decision analyzer failed: {dec_err}")
        
        if success > 0:
            sess = self.Session()
            try:
                triads_after = sess.query(TriadBlock).filter(TriadBlock.user_id == user_id).count()
            finally:
                sess.close()

            crossed_threshold = (triads_before // _REORG_THRESHOLD) < (triads_after // _REORG_THRESHOLD)

            if crossed_threshold:
                logger.info(
                    f"[MindCache] TriadBlock count crossed a {_REORG_THRESHOLD}-job boundary "
                    f"({triads_before} → {triads_after}). Triggering tree reorganisation..."
                )
                from mindcache.Database.reorganize_tree import reorganize_tree
                try:
                    reorganize_tree(user_id=user_id, dry_run=False)
                except Exception as reorg_err:
                    logger.warning(f"[MindCache Warning] Tree reorganisation failed: {reorg_err}")

        if self.enable_summarization:
            logger.info(f"[MindCache] Running recursive summaries for user '{user_id}'...")
            from mindcache.Database.nodes_summary import RecursiveSummarizer
            try:
                RecursiveSummarizer().run(user_id=user_id)
            except Exception as sum_err:
                logger.warning(f"[MindCache Warning] Summarization job failed: {sum_err}")

        # Automatically refresh tree cache after a successful batch run
        if success > 0:
            from mindcache.retrieval.root_cache import refresh_tree_cache, get_tree_cache, CollapsedTreeCache, MemoryMeta
            if crossed_threshold:
                logger.info(f"[MindCache] Reorganization occurred. Rebuilding caches completely for user '{user_id}'...")
                refresh_tree_cache(user_id=user_id)
                tree = get_tree_cache(user_id=user_id)
                cache = CollapsedTreeCache(user_id=user_id)
                cache.build_all(tree, include_summaries=self.enable_summarization)
            else:
                logger.info(f"[MindCache] Updating cache incrementally for user '{user_id}'...")
                from mindcache.Database.embedder import run_memory_embedding_job
                run_memory_embedding_job(user_id=user_id)
                get_tree_cache.cache_clear()
                cache = CollapsedTreeCache(user_id=user_id)
                if not cache.is_ready:
                    cache.load()
                if not cache.is_ready:
                    tree = get_tree_cache(user_id=user_id)
                    cache.build_all(tree, include_summaries=self.enable_summarization)
                else:
                    # Incremental ingestion
                    from mindcache.Database.db_setup import Session, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory, to_numpy
                    db_sess = Session()
                    type_map = {
                        "knowledge": KnowledgeMemory,
                        "episodic":  EpisodicMemory,
                        "user":      UserMemory,
                        "decision":  DecisionMemory,
                    }
                    new_entries = []
                    try:
                        for mem_type, MemClass in type_map.items():
                            query = db_sess.query(MemClass).filter(
                                MemClass.user_id == user_id,
                                MemClass.topic_id.isnot(None)
                            )
                            if mem_type == "decision":
                                query = query.filter(MemClass.status.in_(["active", "conditional"]))
                            for mem in query.all():
                                key = f"mem:{mem_type}:{mem.id}"
                                if key not in cache.data.entry_key_to_index:
                                    # Traverse path
                                    path_parts = []
                                    curr_topic = mem.topic
                                    while curr_topic is not None:
                                        path_parts.append(curr_topic.name or "")
                                        curr_topic = curr_topic.parent
                                    path_str = " > ".join(reversed(path_parts))
                                    searchable = f"{path_str} {mem.content}".strip()
                                    meta = MemoryMeta(
                                        memory_id=mem.id,
                                        memory_type=mem_type,
                                        topic_id=mem.topic_id,
                                        name=mem.topic.name if mem.topic else "",
                                        level=mem.topic.level if mem.topic else 0,
                                        path=path_str,
                                        is_leaf=True,
                                        timestamp=mem.timestamp.strftime("%Y-%m-%d %H:%M") if mem.timestamp else None,
                                        message_id=mem.message_id,
                                        searchable_text=searchable,
                                    )
                                    emb = to_numpy(mem.embedding).copy() if mem.embedding else None
                                    new_entries.append((meta, emb))
                    finally:
                        db_sess.close()

                    if new_entries:
                        logger.info(f"[MindCache] Ingesting {len(new_entries)} new memories incrementally into CollapsedTreeCache.")
                        for meta, emb in new_entries:
                            cache.ingest_memory(meta, embedding=emb)
                    else:
                        logger.info("[MindCache] No new memories to ingest into CollapsedTreeCache.")
                
        return {"success": success, "failed": failed, "tree": get_tree_cache(user_id=user_id)}

    def search(self, query: str, user_id: str = "default", top_k_corpus: int = 30, use_reranker: bool = False) -> str:
        """
        Search for memories matching a query.
        Returns the formatted context string ready to inject into the LLM system prompt.

        Args:
            query:        The query string to search for.
            user_id:      The user ID to scope the search to.
            top_k_corpus: Number of candidates to consider per memory type before reranking.
            use_reranker: If True, applies cross-encoder reranking. Defaults to False for RRF score ordering.
        """
        try:
            result = self.retriever.retrieve(
                query,
                user_id=user_id,
                top_k_corpus=top_k_corpus,
                include_summaries=self.enable_summarization,
                use_reranker=use_reranker,
            )
            return result
        except Exception as e:
            raise RetrievalError(f"Search failed: {e}") from e

    def inspect(self, user_id: str = "default", view: str = "memories", memory_type: str = None) -> list[dict] | dict:
        """
        Developer-facing observability interface to inspect stored memory state.

        Args:
            user_id:      The user ID to inspect.
            view:         Type of inspection view:
                          - 'memories' (default): Returns list of stored memory records.
                          - 'tree': Clears old cache, builds fresh topic tree, and returns it.
                          - 'all': Clears old cache, builds fresh tree, and returns dict with 'tree' and 'memories'.
            memory_type:  Optional filter when viewing memories ('user', 'knowledge', 'episodic', 'decision').
        """
        if view not in ("memories", "tree", "all"):
            raise ValueError(f"Invalid view '{view}'. Allowed options: 'memories', 'tree', 'all'.")

        fresh_tree = None
        if view in ("tree", "all"):
            from mindcache.retrieval.root_cache import refresh_tree_cache, get_tree_cache
            refresh_tree_cache(user_id=user_id)
            fresh_tree = get_tree_cache(user_id=user_id)
            if view == "tree":
                return fresh_tree

        session = self.Session()
        memories = []
        try:
            type_map = {
                "user": UserMemory,
                "knowledge": KnowledgeMemory,
                "episodic": EpisodicMemory,
                "decision": DecisionMemory
            }

            if memory_type is not None and memory_type not in type_map:
                raise ValueError(
                    f"Invalid memory_type '{memory_type}'. "
                    f"Allowed options: {sorted(type_map)}"
                )
            classes_to_query = [type_map[memory_type]] if memory_type and memory_type in type_map else list(type_map.values())

            for MemClass in classes_to_query:
                label = [k for k, v in type_map.items() if v == MemClass][0]
                rows = session.query(MemClass).filter(MemClass.user_id == user_id).all()
                for r in rows:
                    mem_data = {
                        "id": r.id,
                        "type": label,
                        "content": r.content,
                        "topic": r.topic.name if r.topic else "General",
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None
                    }
                    if MemClass == DecisionMemory:
                        mem_data["status"] = r.status
                        mem_data["context"] = r.context
                    memories.append(mem_data)

            if view == "all":
                return {
                    "tree": fresh_tree,
                    "memories": memories
                }
            return memories
        finally:
            session.close()

    def forget(self, memory_id: int, user_id: str = "default") -> bool:
        """
        Remove a memory by its ID for a specific user.
        """
        session = self.Session()
        try:
            reg = session.query(MemoryRegistry).filter(
                MemoryRegistry.id == memory_id,
                MemoryRegistry.user_id == user_id
            ).first()
            if not reg:
                return False

            type_map = {
                "user": UserMemory,
                "knowledge": KnowledgeMemory,
                "episodic": EpisodicMemory,
                "decision": DecisionMemory
            }
            MemClass = type_map.get(reg.memory_type)
            if MemClass:
                session.query(MemClass).filter(
                    MemClass.id == memory_id,
                    MemClass.user_id == user_id
                ).delete()

            session.delete(reg)
            session.commit()

            from mindcache.retrieval.root_cache import CollapsedTreeCache, get_tree_cache
            cache = CollapsedTreeCache(user_id=user_id)
            if cache.is_ready:
                cache.clear()
            get_tree_cache.cache_clear()
            return True
        except Exception as e:
            session.rollback()
            raise IngestionError(f"Forget memory failed: {e}") from e
        finally:
            session.close()

    def reset(self, user_id: str = "default") -> None:
        """
        Delete all MindCache data for a specific user.
        Intentionally destructive operation for testing or user memory resets.
        """
        session = self.Session()
        try:
            session.query(ProcessingJob).filter(ProcessingJob.user_id == user_id).delete()
            session.query(UserMemory).filter(UserMemory.user_id == user_id).delete()
            session.query(KnowledgeMemory).filter(KnowledgeMemory.user_id == user_id).delete()
            session.query(EpisodicMemory).filter(EpisodicMemory.user_id == user_id).delete()
            session.query(DecisionMemory).filter(DecisionMemory.user_id == user_id).delete()
            session.query(MemoryRegistry).filter(MemoryRegistry.user_id == user_id).delete()
            session.query(Topic).filter(Topic.user_id == user_id).delete()
            session.commit()

            from mindcache.retrieval.root_cache import refresh_tree_cache
            refresh_tree_cache(user_id=user_id)
            logger.info(f"[MindCache] Database reset completed for user '{user_id}'.")
        except Exception as e:
            session.rollback()
            raise IngestionError(f"Database reset failed: {e}") from e
        finally:
            session.close()
