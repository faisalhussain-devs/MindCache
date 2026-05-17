import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, TriadBlock, KnowledgeMemory, Topic

Session = sessionmaker(bind=engine)
s = Session()

# Find the exact message containing 536
msgs = s.query(TriadBlock).all()
target_msg = None
for m in msgs:
    if m.source_session_id:
        try:
            ids = json.loads(m.source_session_id)
            if 536 in ids or '536' in ids:
                target_msg = m
                break
        except:
            pass

if target_msg:
    print(f"Found TriadBlock: {target_msg.id} with source_session_id={target_msg.source_session_id}")
    mems = s.query(KnowledgeMemory).filter(KnowledgeMemory.message_id==target_msg.id).all()
    print(f"Memories extracted for this triad: {len(mems)}")
    for m in mems:
        topic = s.get(Topic, m.topic_id) if m.topic_id else None
        print(f"  - Mem ID: {m.id}, Topic ID: {m.topic_id}, Topic Name: {topic.name if topic else None}")
        print(f"    Content: {m.content[:100]}...")
else:
    print("TriadBlock with 536 not found!")

s.close()
