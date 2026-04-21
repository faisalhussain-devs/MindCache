from datetime import datetime
from sqlalchemy.orm import sessionmaker
from Database.db_setup import Topic, engine

def clear_embeddings():
    Session = sessionmaker(bind=engine)
    session = Session()

    # Target timestamp: April 16, 2026, 07:00:00
    target_date = datetime(2026, 4, 16, 7, 0, 0)

    print(f"Looking for topics and embeddings updated after: {target_date}")

    # 1. Clear Topic.embedding
    topics = session.query(Topic).filter(Topic.timestamp >= target_date).all()
    cleared_topic_count = 0
    
    for topic in topics:
        if topic.embedding is not None:
            topic.embedding = None
            cleared_topic_count += 1

            
    session.commit()
    session.close()
    
    print(f"✅ Cleared embeddings for {cleared_topic_count} topics.")

if __name__ == "__main__":
    clear_embeddings()
