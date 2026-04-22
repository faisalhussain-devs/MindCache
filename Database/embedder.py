import numpy as np
from sqlalchemy.orm import sessionmaker
from fastembed import TextEmbedding
from Database.db_setup import engine, Topic

MODEL_NAME = "BAAI/bge-base-en-v1.5" 
MEM_BATCH_SIZE = 24  # Process 32 items at a time to be fast but safe
SUMM_BATCH_SIZE = 8


class EmbeddingManager:
    def __init__(self, dim=512):
        print(f"Loading FastEmbed ONNX Model: {MODEL_NAME}...")
        self.dim = dim
        self.model = TextEmbedding(model_name=MODEL_NAME)
        print(" Embedding Model Loaded (ONNX Native)")

    def encode(self, texts, is_query=False):
        if isinstance(texts, str):
            texts = [texts]

        # BGE requires instruction ONLY for queries
        if is_query:
            instruction = "Represent this sentence for searching relevant passages: "
            texts = [instruction + t for t in texts]

        # FastEmbed handles tokenization and pooling under the hood efficiently
        embeddings = list(self.model.embed(texts))
        
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
        """Convert numpy array to bytes for storage"""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        if hasattr(vector, 'detach'): 
            vector = vector.detach().cpu().numpy()
        return vector.astype(np.float32).tobytes()

def run_embedding_job():
    Session = sessionmaker(bind=engine)
    session = Session()
    embedder = EmbeddingManager()
    try:
        topics = session.query(Topic).filter(
            Topic.description != None, 
            #Topic.embedding == None,
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