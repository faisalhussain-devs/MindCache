from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, ProcessingJob, TriadBlock, Memory, Topic

Session = sessionmaker(bind=engine)
session = Session()

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def inspect_queue():
    print_header("1. PROCESSING QUEUE (Should be Empty)")
    jobs = session.query(ProcessingJob).all()
    if not jobs:
        print(" [OK] Queue is empty.")
    else:
        for job in jobs:
            print(f" [PENDING] Job {job.id} | Status: {job.status}")

def inspect_triad_blocks():
    print_header("2. TRIAD BLOCKS (Conversation Turns)")
    triads = session.query(TriadBlock).order_by(TriadBlock.timestamp.asc()).all()
    for t in triads:
        ts = t.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        print(f" [{t.id}] {ts} | {t.summary} | {t.raw_text}")

def inspect_topics_recursive(parent_id=None, level=0):
    """
    Recursively prints the Topic Tree to show Hierarchy + Summaries.
    """
    # Fetch nodes at this specific level/parent
    if parent_id is None:
        nodes = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
    else:
        nodes = session.query(Topic).filter(Topic.parent_id == parent_id).all()

    for node in nodes:
        indent = "    " * level
        marker = "└──" if level > 0 else "ROOT"
        
        # Check if Summary exists (This verifies your Summarizer Script)
        summary_preview = f"SUMMARY: {node.summary}" if node.summary else "[NO SUMMARY YET]"
        
        print(f"{indent}{marker} [{node.name}] (L{node.level})")
        print(f"{indent}    {summary_preview}")
        
        # Go deeper
        inspect_topics_recursive(node.id, level + 1)

def inspect_memories():
    print_header("4. MEMORY ATOMS (Grouped by Topic)")
    # Query all topics that actually have memories
    topics = session.query(Topic).join(Memory).distinct().all()
    
    for topic in topics:
        mem_count = len(topic.memories)
        print(f"\n---> Topic: {topic.name} ({mem_count} memories)")
        
        for mem in topic.memories:
            type_tag = f"[{mem.type.upper()}]"
            print(f"      {type_tag:<8} {mem.content}")

def run_inspection():
    try:
        inspect_queue()
        inspect_triad_blocks()
        
        print_header("3. TOPIC KNOWLEDGE GRAPH (Hierarchy check)")
        inspect_topics_recursive()
        
        inspect_memories()
        
    except Exception as e:
        print(f"\n[ERROR] Inspection failed: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    run_inspection()