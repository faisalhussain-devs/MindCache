"""
Re-embed root nodes with enriched text (name + description + descendant names).
Run this after modifying embedder.py to use _build_enriched_root_text for roots.

Usage:
    python reembed_roots.py
"""
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

def run():
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        roots = session.query(Topic).filter(Topic.level == 0).all()
        print(f"Found {len(roots)} root nodes.")
        
        # Clear root embeddings so run_embedding_job picks them up
        cleared = 0
        for root in roots:
            if root.embedding is not None:
                root.embedding = None
                cleared += 1
        
        session.commit()
        print(f"Cleared embeddings for {cleared} roots.")
        
        # Now run the embedding job (which will use enriched text for roots)
        from Database.embedder import run_embedding_job
        run_embedding_job()
        
    finally:
        session.close()

if __name__ == "__main__":
    run()
