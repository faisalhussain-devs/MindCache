"""Show descriptions for the TOP 5 heaviest nodes only, in full."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

s = sessionmaker(bind=engine)()

# Get specific heavy nodes by ID
target_ids = [360, 133, 33, 875, 213]  # CRT, RSA, ModArith, KeyGen, Carmichael
names = ["General CRT (380 mems)", "General RSA (232 mems)", "General Modular Arithmetic (226 mems)", 
         "General Key Generation (184 mems)", "General Carmichael (170 mems)"]

for tid, name in zip(target_ids, names):
    topic = s.get(Topic, tid)
    if not topic:
        print(f"Topic {tid} not found")
        continue
    
    desc = topic.description or "(NONE)"
    
    print("=" * 80)
    print(f"{name} — id={tid}")
    print(f"Description length: {len(desc)} chars, ~{len(desc)//4} tokens")
    print("=" * 80)
    
    # Show first 3000 chars
    print(desc[:3000])
    if len(desc) > 3000:
        print(f"\n...[TRUNCATED - {len(desc)} total chars]...\n")
        # Show last 500 chars
        print(f"...LAST 500 CHARS:\n{desc[-500:]}")
    print("\n\n")

s.close()
