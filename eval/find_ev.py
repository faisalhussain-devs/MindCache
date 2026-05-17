import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, TriadBlock, KnowledgeMemory, Topic

Session = sessionmaker(bind=engine)
s = Session()

# Find the message containing 536
msg = s.query(TriadBlock).filter(TriadBlock.source_session_id.like('%536%')).first()
print(f"Found TriadBlock: {msg.id if msg else None} with source_session_id={msg.source_session_id if msg else None}")

if msg:
    mems = s.query(KnowledgeMemory).filter(KnowledgeMemory.message_id==msg.id).all()
    print(f"Memories extracted for this triad: {len(mems)}")
    for m in mems:
        topic = s.get(Topic, m.topic_id) if m.topic_id else None
        print(f"  - Mem ID: {m.id}, Topic ID: {m.topic_id}, Topic Name: {topic.name if topic else None}")
        print(f"    Content: {m.content[:100]}...")

s.close()
