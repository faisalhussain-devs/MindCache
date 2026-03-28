import numpy as np
from sqlalchemy.orm import sessionmaker
from sentence_transformers import SentenceTransformer
from Database.db_setup import engine, Topic

MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B" 
MEM_BATCH_SIZE = 24  # Process 32 items at a time to be fast but safe
SUMM_BATCH_SIZE = 8

class EmbeddingManager:
    def __init__(self):
        print(f"Loading Embedding Model: {MODEL_NAME}...")
        self.model = SentenceTransformer(MODEL_NAME, trust_remote_code=True)
        self.model.max_seq_length = 2048
        print(" Model Loaded.")

    def get_batch_embeddings(self, text_list):
        """Generates vectors for a list of strings."""
        if not text_list:
            return None
        
        # Specific prompt for Retrieval tasks (adjust if Qwen docs say otherwise)
        instruction = "Instruct: Represent this text for retrieval so it can be retreived accurately"
        
        embeddings = self.model.encode(
            text_list,
            prompt=instruction,
            normalize_embeddings=True, # Crucial for Cosine Similarity
            show_progress_bar=False
        )
        return embeddings
    
    def _to_blob(self, vector):
        """Convert numpy array to bytes for storage"""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        if hasattr(vector, 'detach'): 
            vector = vector.detach().cpu().numpy()
        return vector.astype(np.float32).tobytes()

def _build_enriched_root_text(session, root):
    """Build embedding text for a root: name + all descendant names (3 levels deep)."""
    parts = [root.name]
    if root.description:
        parts.append(root.description)
    
    descendant_names = []
    def _collect_names(node, depth=0, max_depth=1):
        if depth >= max_depth:
            return
        for child in node.children:
            descendant_names.append(child.name)
            _collect_names(child, depth + 1, max_depth)
    
    _collect_names(root)
    if descendant_names:
        parts.append("Sub-topics: " + ", ".join(descendant_names))
    
    return ". ".join(parts)


def run_embedding_job():
    Session = sessionmaker(bind=engine)
    session = Session()
    embedder = EmbeddingManager()

    try:
        topics = session.query(Topic).filter(
            Topic.description != None, 
            Topic.embedding == None,
        ).all()
        
        total_topics = len(topics)
        print(f"Found {total_topics} topics needing vectors.")

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
                print(f" -> Processed {i + len(batch)}/{total_topics} topics...")

        print("\n Embedding Job Complete!")

    except Exception as e:
        session.rollback()
        print(f"\n Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    run_embedding_job()