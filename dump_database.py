import os
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory

def dump_database_to_file(output_file="mindcache_database_dump.txt"):
    Session = sessionmaker(bind=engine)
    session = Session()

    print(f"Opening database and writing full view to {output_file}...")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("==================================================\n")
        f.write("          MINDCACHE COMPLETE DATABASE DUMP         \n")
        f.write("==================================================\n\n")

        # Get all root topics
        roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()

        if not roots:
            f.write("No topics or memories found in the database.\n")
            
        def write_topic_recursive(topic, depth=0):
            # Indentation
            indent = "    " * depth
            
            # Print Topic Header
            f.write(f"\n{indent}📂 [{topic.name}] (Level {topic.level}, ID: {topic.id})\n")
            
            # Sub-indent for memories
            m_indent = indent + "  |-- "
            
            has_memories = False

            # 1. Facts / Knowledge
            for mem in topic.knowledge_memories:
                has_memories = True
                f.write(f"{m_indent}[FACT] (Msg ID: {mem.message_id}) {mem.content}\n")
                
            # 2. User Memories
            for mem in topic.user_memories:
                has_memories = True
                f.write(f"{m_indent}[USER] (Msg ID: {mem.message_id}) {mem.content}\n")

            # 3. Episodic Memories
            for mem in topic.episodic_memories:
                has_memories = True
                f.write(f"{m_indent}[EPISODIC] (Msg ID: {mem.message_id}) {mem.content}\n")

            # 4. Decision Memories
            for mem in topic.decision_memories:
                has_memories = True
                status_tag = f" ({mem.status.upper()})" if mem.status else ""
                f.write(f"{m_indent}[DECISION]{status_tag} (Msg ID: {mem.message_id}) {mem.content}\n")

            if not has_memories and depth > 0:
                # Optionally note if it's an empty structural node
                pass
                
            # Recurse children
            for child in topic.children:
                write_topic_recursive(child, depth + 1)

        for root in roots:
            write_topic_recursive(root, 0)
            f.write("\n" + "="*50 + "\n")

    session.close()
    print(f"Success! The active database tree has been saved to: {os.path.abspath(output_file)}")

if __name__ == "__main__":
    dump_database_to_file()
