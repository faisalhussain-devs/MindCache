from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

def print_tree(node, prefix=""):
    """Recursively print the topic tree"""

    total_memories = len(node.episodic_memories) + len(node.user_memories) + len(node.knowledge_memories) + len(node.decision_memories)
    mem = f" ({total_memories} memories)" if not node.children else ""
    if node.level == 0:  
        print(f"[{node.level}:{node.id}] {node.name}{mem}")
    else:
        print(f"{prefix}└── [{node.level}:{node.id}] {node.name}{mem}")
    
    # Calculate prefix for children
    child_prefix = prefix + "    " if node.level > 0 else "    "
    
    children = sorted(node.children, key=lambda x: x.name)
    
    for i, child in enumerate(children):
        print_tree(child, child_prefix)

def main():
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Fetch all root nodes (level 0, parent_id is None)
    root_nodes = session.query(Topic).filter(Topic.parent_id.is_(None)).order_by(Topic.name).all()
    
    if not root_nodes:
        print("The Topic tree is currently empty.")
        session.close()
        return
        
    print(f"--- Topic Tree ({len(root_nodes)} roots) ---\n")
    
    for root in root_nodes:
        print_tree(root)
        print("\n") # blank line between roots
        
    session.close()

if __name__ == "__main__":
    main()
