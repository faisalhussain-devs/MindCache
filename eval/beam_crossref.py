"""Cross-reference: What the BEAM questions need vs what was extracted."""
import json, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from Database.db_setup import engine

# Load BEAM data
with open('BEAM/eval_data_beam.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

tcs = data['test_cases']

# For each question, find the referenced turns, look at the raw message that
# contains that turn, and check what memories were extracted

with engine.connect() as conn:
    # For a few key questions, trace the evidence chain
    
    # Q7: "How many prime factorization problems had I completed when I mentioned my accuracy rate?"
    # Answer: 5 with 80% accuracy
    # Turns: [14] in sess_0
    
    print("=" * 80)
    print("Q7: How many prime factorization problems had I completed (Answer: 5, 80%)")
    print("Referenced turn: 14 in sess_0")
    print("-" * 40)
    
    # Find the actual turn content
    sessions = data['sessions']
    for sess in sessions:
        if sess['id'] == 'sess_0':
            for turn in sess['content']:
                if turn['turn_id'] == 14:
                    print(f"RAW TURN 14: {turn['content'][:200]}")
            break
    
    # Now search memories for the evidence "5 problems" or "80%"
    for tbl in ['memories_knowledge', 'memories_episodic', 'memories_user']:
        result = conn.execute(text(f"SELECT id, content, topic_id FROM {tbl} WHERE content LIKE '%80%' AND content LIKE '%accuracy%'"))
        mems = result.fetchall()
        print(f"\n  [{tbl}] Matches for '80% accuracy': {len(mems)}")
        for m in mems[:5]:
            print(f"    id={m[0]} topic={m[2]}: {m[1][:150]}")
    
    # Q8: "What were the two pairs for GCD?" → (48,18) and (270,192)
    print("\n" + "=" * 80)
    print("Q8: Two pairs for GCD (Answer: (48,18) and (270,192))")
    print("Referenced turn: 26 in sess_0")
    print("-" * 40)
    
    for sess in sessions:
        if sess['id'] == 'sess_0':
            for turn in sess['content']:
                if turn['turn_id'] == 26:
                    print(f"RAW TURN 26: {turn['content'][:300]}")
            break
    
    for tbl in ['memories_knowledge', 'memories_episodic', 'memories_user']:
        result = conn.execute(text(f"SELECT id, content, topic_id FROM {tbl} WHERE content LIKE '%270%' AND content LIKE '%192%'"))
        mems = result.fetchall()
        print(f"\n  [{tbl}] Matches for '270,192': {len(mems)}")
        for m in mems[:5]:
            print(f"    id={m[0]} topic={m[2]}: {m[1][:150]}")
    
    # Q1: "What feedback after correcting factoring 56?"
    print("\n" + "=" * 80)
    print("Q1: Feedback after correcting factoring 56 (Answer: no feedback in chat)")
    print("-" * 40)
    
    for tbl in ['memories_knowledge', 'memories_episodic', 'memories_user']:
        result = conn.execute(text(f"SELECT id, content, topic_id FROM {tbl} WHERE content LIKE '%56%' AND content LIKE '%factor%'"))
        mems = result.fetchall()
        print(f"\n  [{tbl}] Matches for '56 factor': {len(mems)}")
        for m in mems[:5]:
            print(f"    id={m[0]} topic={m[2]}: {m[1][:150]}")

    # Q12: "How many RSA problems with p=71, q=59?" → 12
    print("\n" + "=" * 80)
    print("Q12: How many RSA problems with p=71, q=59? (Answer: 12)")
    print("-" * 40)
    
    for tbl in ['memories_knowledge', 'memories_episodic', 'memories_user']:
        result = conn.execute(text(f"SELECT id, content, topic_id FROM {tbl} WHERE content LIKE '%71%' AND content LIKE '%59%'"))
        mems = result.fetchall()
        print(f"\n  [{tbl}] Matches for 'p=71 q=59': {len(mems)}")
        for m in mems[:5]:
            print(f"    id={m[0]} topic={m[2]}: {m[1][:150]}")

    # NOW: Show total DUPLICATE memories (same content across different message_ids)
    print("\n" + "=" * 80)
    print("DUPLICATION ANALYSIS")
    print("=" * 80)
    
    for tbl in ['memories_knowledge', 'memories_episodic']:
        result = conn.execute(text(f"""
            SELECT content, COUNT(*) as cnt
            FROM {tbl}
            GROUP BY content
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 10
        """))
        dups = result.fetchall()
        total_dup_rows = sum(d[1] for d in dups)
        print(f"\n  [{tbl}] Top 10 exact duplicates (total dup rows in top 10: {total_dup_rows}):")
        for d in dups:
            print(f"    x{d[1]}: {d[0][:120]}")
    
    # Count total exact duplicates
    for tbl in ['memories_knowledge', 'memories_episodic']:
        result = conn.execute(text(f"""
            SELECT SUM(cnt - 1) FROM (
                SELECT COUNT(*) as cnt FROM {tbl} GROUP BY content HAVING COUNT(*) > 1
            )
        """))
        excess = result.scalar() or 0
        result2 = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
        total = result2.scalar()
        print(f"\n  [{tbl}]: {total} total, {excess} EXACT duplicates ({100*excess//total}% redundant)")
