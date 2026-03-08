import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.db_setup import engine, Topic, TopicEmbeddingCache
from sqlalchemy.orm import sessionmaker

def undo_sibling_splits():
    Session = sessionmaker(bind=engine)
    session = Session()

    # Find all nodes that start with "General "
    general_nodes = session.query(Topic).filter(Topic.name.like("General %")).all()
    
    reverted_count = 0
    
    for gen_node in general_nodes:
        original_name = gen_node.name.replace("General ", "", 1)
        
        # Find the sibling that took the original name
        # It should have the same parent_id
        sibling_parent = session.query(Topic).filter(
            Topic.name == original_name,
            Topic.parent_id == gen_node.parent_id
        ).first()
        
        if sibling_parent:
            print(f"Reverting: {gen_node.name} (ID {gen_node.id}) and Sibling Parent {sibling_parent.name} (ID {sibling_parent.id})")
            
            # 1. Move all children from sibling_parent back to gen_node
            for child in list(sibling_parent.children):
                child.parent = gen_node
                
            # 2. Restore gen_node's original name
            gen_node.name = original_name
            
            # 3. Delete the sibling_parent
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == sibling_parent.id).delete()
            session.delete(sibling_parent)
            
            reverted_count += 1
            
    session.commit()
    print(f"Successfully reverted {reverted_count} leaf node enforcing sibling splits.")

if __name__ == "__main__":
    undo_sibling_splits()
