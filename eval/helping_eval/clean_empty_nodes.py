from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

def clean_empty_nodes():
    Session = sessionmaker(bind=engine)
    session = Session()
    
    deleted_count = 0
    while True:
        # Fetch all nodes in current state
        all_nodes = session.query(Topic).all()
        nodes_to_delete = []
        
        for node in all_nodes:
            # Check if it has no children
            if not node.children:
                # Check if it has no memories of any type
                total_mems = len(node.episodic_memories) + len(node.user_memories) + len(node.knowledge_memories) + len(node.decision_memories)
                if total_mems == 0:
                    nodes_to_delete.append(node)
        
        if not nodes_to_delete:
            break
            
        for node in nodes_to_delete:
            print(f"Deleting empty node: {node.name} (ID: {node.id})")
            session.delete(node)
            deleted_count += 1
            
        session.commit()
        
    print(f"\nCleanup complete. Deleted {deleted_count} empty nodes.")
    session.close()

if __name__ == "__main__":
    clean_empty_nodes()
