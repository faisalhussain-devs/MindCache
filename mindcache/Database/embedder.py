import os
import threading
import numpy as np
from sqlalchemy.orm import sessionmaker
from fastembed import TextEmbedding
from mindcache.Database.db_setup import Topic, Session
import logging
logger = logging.getLogger(__name__)

MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5" 
# Store model in project dir so Windows Temp cleanup never deletes it
MODEL_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".model_cache")
MODEL_LOCK = threading.Lock()
MEM_BATCH_SIZE = 24  # Process 32 items at a time to be fast but safe
SUMM_BATCH_SIZE = 8


class EmbeddingManager:
    _instance = None
    _model = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with MODEL_LOCK:
                if cls._instance is None:
                    cls._instance = super(EmbeddingManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, dim=768):
        if self._initialized:
            return
        self.dim = dim
        self._initialized = True

    @property
    def model(self):
        if EmbeddingManager._model is None:
            with MODEL_LOCK:
                    try:
                        EmbeddingManager._model = TextEmbedding(model_name=MODEL_NAME, cache_dir=MODEL_CACHE_DIR, providers=["DmlExecutionProvider"])
                        logger.info(" Embedding Model Loaded (ONNX with DirectML GPU)")
                    except Exception as dml_err:
                        logger.info(f" DirectML initialization failed ({dml_err}). Falling back to CPU...")
                        EmbeddingManager._model = TextEmbedding(model_name=MODEL_NAME, cache_dir=MODEL_CACHE_DIR)
                        logger.info(" Embedding Model Loaded (ONNX Native CPU)")
        return EmbeddingManager._model

    def encode(self, texts, is_query=False):
        if isinstance(texts, str):
            texts = [texts]

        # Truncate very long documents to prevent ONNX runtime OOM errors on CPU
        truncated_texts = []
        for t in texts:
            words = t.split()
            if len(words) > 1000:
                t = " ".join(words[:1000])
            truncated_texts.append(t)
        texts = truncated_texts

        # Nomic requires task prefixes
        if is_query:
            texts = ["search_query: " + t for t in texts]
        else:
            texts = ["search_document: " + t for t in texts]

        # Process one-by-one to avoid ONNX OOM on long context inputs.
        # Each text can be up to 8000 chars (~2000 tokens). Splicing with batch_size=1
        # ensures minimal memory footprint in ONNX Runtime.
        SUB_BATCH = 1
        all_embeddings = []
        for i in range(0, len(texts), SUB_BATCH):
            chunk = texts[i : i + SUB_BATCH]
            all_embeddings.extend(self.model.embed(chunk, batch_size=1))
        embeddings = all_embeddings
        
        # Convert to a stable numpy matrix to slice dims
        embeddings_matrix = np.array(embeddings)
        
        # Slice for Matryoshka dimension reduction
        embeddings_matrix = embeddings_matrix[:, :self.dim]
        
        # Re-normalize mathematically via NumPy after slicing
        norms = np.linalg.norm(embeddings_matrix, axis=1, keepdims=True)
        embeddings_matrix = embeddings_matrix / np.where(norms == 0, 1e-10, norms)

        return embeddings_matrix

    def get_batch_embeddings(self, text_list):
        """Generates vectors for a list of strings."""
        if not text_list:
            return None
        embeddings = self.encode(text_list)
        return embeddings
    
    def _to_blob(self, vector):
        """Convert numpy array to bytes (or list for postgres) for storage"""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        if hasattr(vector, 'detach'): 
            vector = vector.detach().cpu().numpy()
        from mindcache.Database.db_setup import is_postgres
        if is_postgres:
            return vector.astype(np.float32).tolist()
        return vector.astype(np.float32).tobytes()

_embedder_instance = None

def get_embedder():
    global _embedder_instance
    if _embedder_instance is None:
        _embedder_instance = EmbeddingManager()
    return _embedder_instance

def run_embedding_job(user_id="default"):
    session = Session()
    embedder = EmbeddingManager()
    try:
        topics = session.query(Topic).filter(
            Topic.user_id == user_id,
            Topic.description != None, 
            Topic.embedding == None,
        ).all()
        
        total_topics = len(topics)
        logger.info(f"Found {total_topics} topics needing vectors for user {user_id}.")

        if total_topics > 0:
            for i in range(0, total_topics, SUMM_BATCH_SIZE):
                batch = topics[i : i + SUMM_BATCH_SIZE]
                
                # Build embedding text: enriched for roots, description for others
                texts = []
                for t in batch:
                    texts.append(t.description)
                
                vectors = embedder.get_batch_embeddings(texts)
                
                # Convert Numpy arrays to bytes
                vectors = [embedder._to_blob(vec) for vec in vectors]
                
                for topic, vec in zip(batch, vectors):
                    topic.embedding = vec
                
                session.commit()
                logger.info(f" -> Processed {i + len(batch)}/{total_topics} topics...")

        logger.info(f"\n Embedding Job Complete for user {user_id}!")

    except Exception as e:
        session.rollback()
        logger.error(f"\n Error: {e}")
    finally:
        session.close()

def run_memory_embedding_job(user_id="default"):
    """
    Embed all un-embedded individual memory rows across all 4 memory tables.
    Stores each vector as a LargeBinary blob in the memory row's `embedding` column.
    Safe to re-run — only processes rows where embedding IS NULL.
    """
    from mindcache.Database.db_setup import KnowledgeMemory, EpisodicMemory, UserMemory, DecisionMemory, Topic
    session = Session()
    embedder = EmbeddingManager()

    type_map = [
        ("knowledge", KnowledgeMemory, []),
        ("episodic",  EpisodicMemory,  []),
        ("user",      UserMemory,      []),
        ("decision",  DecisionMemory,  [DecisionMemory.status.in_(["active", "conditional"])]),
    ]

    try:
        # Build path map for all topics for this user to avoid queries in loop
        topics = session.query(Topic).filter(Topic.user_id == user_id).all()
        topic_map_db = {t.id: t for t in topics}
        
        def get_path(topic_id) -> str:
            parts = []
            curr_id = topic_id
            while curr_id is not None:
                topic = topic_map_db.get(curr_id)
                if not topic:
                    break
                parts.append(topic.name or "")
                curr_id = topic.parent_id
            return " > ".join(reversed(parts))

        for label, MemClass, extra_filters in type_map:
            query = session.query(MemClass).filter(
                MemClass.user_id == user_id,
                MemClass.content.isnot(None),
                MemClass.embedding.is_(None),
            )
            for f in extra_filters:
                query = query.filter(f)
            rows = query.all()
            total = len(rows)
            if total == 0:
                logger.info(f"  [{label}] All memories already embedded for user {user_id}.")
                continue
            logger.info(f"  [{label}] Embedding {total} memories for user {user_id}...")
            for i in range(0, total, MEM_BATCH_SIZE):
                batch = rows[i:i + MEM_BATCH_SIZE]
                texts = [r.content for r in batch]
                vecs = embedder.get_batch_embeddings(texts)
                if vecs is None:
                    continue
                for row, vec in zip(batch, vecs):
                    row.embedding = embedder._to_blob(vec)
                session.commit()
                logger.info(f"    -> {min(i + MEM_BATCH_SIZE, total)}/{total}")
        logger.info(f"\nMemory Embedding Job Complete for user {user_id}!")
    except Exception as e:
        session.rollback()
        logger.error(f"\nError: {e}")
        raise
    finally:
        session.close()


def _partition_text(text: str, target_chars: int = 4000) -> list[str]:
    """
    Partition a conversational log into chunks of complete (User, Assistant) turn pairs
    targeting target_chars (~1000 tokens). Keeps turns together and ensures each
    partition ends with an Assistant turn.
    """
    import re
    from mindcache.Database.db_manager import DatabaseManager
    clean_text = DatabaseManager._strip_timestamps(text)
    lines = clean_text.split("\n")
    turns = []
    current_turn_role = None
    current_turn_lines = []

    role_pattern = re.compile(r'^(User|Assistant):\s*(.*)', re.IGNORECASE)

    for line in lines:
        match = role_pattern.match(line)
        if match:
            if current_turn_role and current_turn_lines:
                turns.append((current_turn_role, "\n".join(current_turn_lines)))
            current_turn_role = match.group(1).capitalize()
            current_turn_lines = [match.group(2)]
        else:
            if current_turn_role:
                current_turn_lines.append(line)

    if current_turn_role and current_turn_lines:
        turns.append((current_turn_role, "\n".join(current_turn_lines)))

    # Group turns into complete User-Assistant pairs
    pairs = []
    i = 0
    while i < len(turns):
        if turns[i][0] == "User":
            user_text = f"User: {turns[i][1]}"
            assistant_text = ""
            if i + 1 < len(turns) and turns[i+1][0] == "Assistant":
                assistant_text = f"\nAssistant: {turns[i+1][1]}"
                i += 2
            else:
                i += 1
            pairs.append(user_text + assistant_text)
        else:
            # Fallback: Assistant turn without leading User (e.g. at start of chunk)
            pairs.append(f"Assistant: {turns[i][1]}")
            i += 1

    if not pairs:
        if clean_text:
            return [clean_text]
        return []

    partitions = []
    current_partition_parts = []
    current_len = 0

    for pair in pairs:
        current_partition_parts.append(pair)
        current_len += len(pair)
        # If the current chunk exceeds target_chars, cut here
        if current_len >= target_chars:
            partitions.append("\n".join(current_partition_parts))
            current_partition_parts = []
            current_len = 0

    # Merge trailing chunk if too small (< 100 tokens / 400 chars)
    if current_partition_parts:
        trailing = "\n".join(current_partition_parts)
        if partitions and len(trailing) < 400:
            partitions[-1] = partitions[-1] + "\n" + trailing
        else:
            partitions.append(trailing)

    return partitions


def _batch_embed_pending_jobs(pending, embedder):
    """
    Pre-compute multi-vector query embeddings for all pending jobs that don't have one yet.
    Partitions the conversational log into complete (user, assistant) turn pairs
    closest to 1000 tokens (~4000 characters).
    """
    import numpy as np
    from mindcache.Database.db_setup import ProcessingJob

    needs_embed = [j for j in pending if j.embedding is None]
    if not needs_embed:
        logger.info(f"[Embed] All {len(pending)} jobs already have embeddings.")
        return

    logger.info(f"[Embed] Computing partitioned multi-vector embeddings for {len(needs_embed)} jobs...")
    db_session = Session()
    try:
        for job in needs_embed:
            # 1. Partition the prompt into ~1000 token segments of turn pairs
            partitions = _partition_text(job.raw_prompt)
            if not partitions:
                continue

            # 2. Embed the partitions as a batch using document mode
            vecs = embedder.encode(partitions, is_query=False)  # shape (N, 768)

            # 3. Serialize the (N, 768) float32 matrix directly to bytes
            job.embedding = vecs.astype(np.float32).tobytes()

            # Persist to DB
            db_job = db_session.get(ProcessingJob, job.id)
            if db_job:
                db_job.embedding = job.embedding
            db_session.commit()
            logger.info(f"[Embed] Job {job.id}: partitioned into {len(partitions)} chunks and embedded.")
    except Exception as e:
        db_session.rollback()
        logger.warning(f"[Embed] Warning: batch embedding failed ({e}). Jobs will be embedded on-demand.")
    finally:
        db_session.close()


if __name__ == "__main__":
    run_embedding_job()
    run_memory_embedding_job()