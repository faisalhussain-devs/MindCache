from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

def print_tree(node, prefix=""):
    """Recursively print the topic tree"""

    if node.level == 0:
        mem = ""
        if not node.children:
            mem = f" ({len(node.episodic_memories) + len(node.user_memories) + len(node.knowledge_memories) + len(node.decision_memories)} memories)"
        print(f"[{node.level}] {node.name}{mem}")
    elif not node.children:
        total_memories = len(node.episodic_memories) + len(node.user_memories) + len(node.knowledge_memories) + len(node.decision_memories)
        print(f"{prefix}└── [{node.level}] {node.name} ({total_memories} memories)")
    else:
        # Determine appropriate connector (└─ or ├─)
        print(f"{prefix}└── [{node.level}] {node.name}")
    
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
