import os
import json
from datetime import datetime

from mindcache.Database.db_setup import init_db, ProcessingJob, MemoryRegistry, Topic, UserMemory, KnowledgeMemory, EpisodicMemory, DecisionMemory, Session
from mindcache.exceptions import IngestionError, RetrievalError, ConfigurationError
from mindcache.Database.db_manager import DatabaseManager
from mindcache.retrieval.active_path import ActivePathRetrieval
from mindcache.Memory_extract.memory_extractor import Memory_Extractor
import logging
logger = logging.getLogger(__name__)
REORG_THRESHOLD = 60

class MindCache:
    """
    MindCache — Structured Long-Term Memory SDK for LLM Agents.
    Provides persistent, hierarchical memory with zero-copy vector search.
    
    Usage:
        mc = MindCache(db_path="mindcache.db", provider="gemini", model_name="gemini-2.5-flash")
        
        # Ingestion (non-blocking, writes to queue in milliseconds)
        mc.add([
            {"role": "user", "content": "I prefer working with Python and FastAPI."},
            {"role": "assistant", "content": "Got it! We will focus on Python and FastAPI."}
        ], user_id="alice")
        
        # Process the queue in background
        mc.process_queue(user_id="alice")
        
        # Retrieve context
        context = mc.search("What are my preferred tools?", user_id="alice")
    """
    def __init__(
        self,
        db_path: str = None,
        gemini_api_key: str = None,
        provider: str = "gemini",
        model_name: str = "gemini-2.5-flash",
        enable_summarization: bool = False
    ):
        if db_path:
            os.environ["MINDCACHE_DB_PATH"] = db_path
            from mindcache.Database.db_setup import reconfigure_engine
            reconfigure_engine(db_path)
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
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "").strip()
            lines.append(f"{role}: {content}")
        raw_prompt = "\n".join(lines)
        
        turn_ids = [msg.get("turn_id") for msg in messages if msg.get("turn_id") is not None]
        turn_ids_str = json.dumps(turn_ids) if turn_ids else "[]"
        
        session = self.Session()
        try:
            new_job = ProcessingJob(
                raw_prompt=raw_prompt,
                turn_ids=turn_ids_str,
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

    def process_queue(self, user_id: str = "default", limit: int = None) -> dict:
        """
        Run the memory extraction pipeline on queued jobs for a specific user.
        Processes up to `limit` jobs.
        """
        try:
            session = self.Session()
            try:
                query = session.query(ProcessingJob).filter(
                    ProcessingJob.status == "pending",
                    ProcessingJob.user_id == user_id
                ).order_by(ProcessingJob.id)
                
                if limit:
                    query = query.limit(limit)
                pending_jobs = query.all()
                
                if not pending_jobs:
                    logger.info(f"[MindCache] No pending jobs in the queue for user '{user_id}'.")
                    return {"success": 0, "failed": 0}
                
                # Pre-compute query embeddings for the batch
                from mindcache.Database.embedder import _batch_embed_pending_jobs
                _batch_embed_pending_jobs(pending_jobs, self.db_manager._get_embedder())
                
                # Refresh session to load embeddings
                session.expire_all()
                
                # Detach pending jobs data and close session immediately to avoid transaction locking contention
                jobs_data = []
                for job in pending_jobs:
                    jobs_data.append({
                        "id": job.id,
                        "raw_prompt": job.raw_prompt,
                        "embedding": job.embedding,
                        "timestamp": job.timestamp
                    })
            finally:
                session.close()
        except Exception as e:
            raise IngestionError(f"Failed to retrieve pending jobs from queue: {e}") from e
            
        # --- Snapshot TriadBlock count BEFORE processing ---
        # This is used at the end to check if we crossed a REORG_THRESHOLD boundary.
        # Using TriadBlock rows (one per successfully ingested job) rather than `success`
        # counter avoids mismatch when jobs are skipped or create no memories.
        from mindcache.Database.db_setup import TriadBlock
        _snap_sess = self.Session()
        try:
            triads_before = _snap_sess.query(TriadBlock).filter(TriadBlock.user_id == user_id).count()
        finally:
            _snap_sess.close()

        success = 0
        failed = 0
        for job_data in jobs_data:
            job_id = job_data["id"]
            try:
                # Run memory extraction
                extracted_data = self.extractor.memory_extract(job_data["raw_prompt"], query_embedding=job_data["embedding"])
                
                if not extracted_data:
                    # Open a short-lived session to mark job as failed
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
                
                # Save extracted memories (this opens and manages its own session)
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
                # Open a short-lived session to mark job as failed
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
        
        # --- Check if we crossed a REORG_THRESHOLD boundary this run ---
        # Algorithm:
        #   triads_before = TriadBlock count before this process_queue() call
        #   triads_after  = TriadBlock count after all jobs in this call are saved
        #
        #   We divide each by REORG_THRESHOLD using integer floor division.
        #   If the quotient increased, we crossed at least one 60-job boundary.
        #
        #   Example across multiple runs (REORG_THRESHOLD = 60):
        #     Run 1: before=0,  after=20  → floor(0/60)=0, floor(20/60)=0  → NO reorg
        #     Run 2: before=20, after=35  → floor(20/60)=0, floor(35/60)=0 → NO reorg
        #     Run 3: before=35, after=40  → floor(35/60)=0, floor(40/60)=0 → NO reorg
        #     Run 4: before=40, after=65  → floor(40/60)=0, floor(65/60)=1 → REORG ✅
        #     Run 5: before=65, after=75  → floor(65/60)=1, floor(75/60)=1 → NO reorg
        #     Run 7: before=95, after=125 → floor(95/60)=1, floor(125/60)=2 → REORG ✅
        #
        # Sequence: Reorganisation runs first, then Summarisation.
        if success > 0:
            sess = self.Session()
            try:
                triads_after = sess.query(TriadBlock).filter(TriadBlock.user_id == user_id).count()
            finally:
                sess.close()

            crossed_threshold = (triads_before // REORG_THRESHOLD) < (triads_after // REORG_THRESHOLD)

            if crossed_threshold:
                logger.info(
                    f"[MindCache] TriadBlock count crossed a {REORG_THRESHOLD}-job boundary "
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
            logger.info(f"[MindCache] Successfully processed {success} jobs. Refreshing tree cache...")
            from mindcache.retrieval.root_cache import refresh_tree_cache
            refresh_tree_cache(user_id=user_id)
                
        return {"success": success, "failed": failed}

    def search(self, query: str, user_id: str = "default", top_k_corpus: int = 30) -> str:
        """
        Search for memories matching a query.
        Returns the formatted context string ready to inject into the LLM system prompt.
        """
        try:
            result = self.retriever.retrieve(query, user_id=user_id, top_k_corpus=top_k_corpus, include_summaries=self.enable_summarization)
            return result.context
        except Exception as e:
            raise RetrievalError(f"Search failed: {e}") from e

    def get_all(self, user_id: str = "default", memory_type: str = None) -> list[dict]:
        """
        Fetch all stored memories from the database.
        Optionally filter by memory_type: 'user', 'knowledge', 'episodic', or 'decision'.
        """
        session = self.Session()
        memories = []
        try:
            type_map = {
                "user": UserMemory,
                "knowledge": KnowledgeMemory,
                "episodic": EpisodicMemory,
                "decision": DecisionMemory
            }
            
            classes_to_query = [type_map[memory_type]] if memory_type else type_map.values()
            
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
            return memories
        finally:
            session.close()

    def delete(self, memory_id: int, user_id: str = "default") -> bool:
        """
        Delete a memory by its ID.
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
            
            from mindcache.retrieval.root_cache import refresh_tree_cache
            refresh_tree_cache(user_id=user_id)
            return True
        except Exception as e:
            session.rollback()
            raise IngestionError(f"Delete memory failed: {e}") from e
        finally:
            session.close()

    def reset(self, user_id: str = "default") -> None:
        """
        Reset the database for a specific user, clearing all memories and queue jobs.
        """
        session = self.Session()
        try:
            session.query(ProcessingJob).filter(ProcessingJob.user_id == user_id).delete()
            session.query(MemoryRegistry).filter(MemoryRegistry.user_id == user_id).delete()
            session.query(UserMemory).filter(UserMemory.user_id == user_id).delete()
            session.query(KnowledgeMemory).filter(KnowledgeMemory.user_id == user_id).delete()
            session.query(EpisodicMemory).filter(EpisodicMemory.user_id == user_id).delete()
            session.query(DecisionMemory).filter(DecisionMemory.user_id == user_id).delete()
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