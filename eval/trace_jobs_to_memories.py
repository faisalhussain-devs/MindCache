"""Trace: raw message → extracted memories. Show if extraction is excessive."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from Database.db_setup import engine
import json

with engine.connect() as conn:
    # Sample 3 messages and ALL their extracted memories
    for msg_id in [1, 2, 5, 10, 50]:
        # Get the raw message
        result = conn.execute(text("SELECT id, raw_msg, timestamp FROM messages WHERE id = :mid"), {"mid": msg_id})
        row = result.fetchone()
        if not row:
            continue
        
        raw_msg = row[1]
        # Truncate for display
        display_msg = raw_msg[:500] if len(raw_msg) > 500 else raw_msg
        total_msg_chars = len(raw_msg)
        
        print("=" * 80)
        print(f"MESSAGE #{msg_id} (timestamp: {row[2]}, {total_msg_chars} chars, ~{total_msg_chars//4} tokens)")
        print(f"RAW MSG (first 500 chars):\n{display_msg}")
        print("-" * 40)
        
        # Get ALL extracted memories for this message_id
        total_extracted = 0
        for tbl, mtype in [('memories_knowledge', 'KNOWLEDGE'), ('memories_episodic', 'EPISODIC'), 
                           ('memories_user', 'USER'), ('memories_decision', 'DECISION')]:
            result = conn.execute(
                text(f"SELECT id, content, topic_id FROM {tbl} WHERE message_id = :mid"),
                {"mid": msg_id}
            )
            mems = result.fetchall()
            if mems:
                print(f"\n  [{mtype}] {len(mems)} memories extracted:")
                for j, mem in enumerate(mems):
                    content = mem[1][:120]
                    print(f"    {j+1}. (topic={mem[2]}) {content}")
                total_extracted += len(mems)
        
        print(f"\n  >> TOTAL: {total_extracted} memories from 1 message ({total_msg_chars} chars input → {total_extracted} extractions)")
        print()
    
    # Summary stats
    print("\n" + "=" * 80)
    print("EXTRACTION RATIO ANALYSIS")
    print("=" * 80)
    
    result = conn.execute(text("""
        SELECT m.id, LENGTH(m.raw_msg) as msg_len,
            (SELECT COUNT(*) FROM memories_knowledge WHERE message_id = m.id) +
            (SELECT COUNT(*) FROM memories_episodic WHERE message_id = m.id) +
            (SELECT COUNT(*) FROM memories_user WHERE message_id = m.id) +
            (SELECT COUNT(*) FROM memories_decision WHERE message_id = m.id) as total_mems
        FROM messages m
        ORDER BY m.id
    """))
    rows = result.fetchall()
    
    total_msgs = len(rows)
    total_mems = sum(r[2] for r in rows)
    avg_per_msg = total_mems / total_msgs if total_msgs > 0 else 0
    max_mems = max(r[2] for r in rows) if rows else 0
    max_msg_id = [r[0] for r in rows if r[2] == max_mems][0] if rows else 0
    
    print(f"Total messages: {total_msgs}")
    print(f"Total memories extracted: {total_mems}")
    print(f"Average memories per message: {avg_per_msg:.1f}")
    print(f"Max memories from single message: {max_mems} (message #{max_msg_id})")
    
    # Distribution
    buckets = {
        '0-10': 0, '11-20': 0, '21-30': 0, '31-50': 0, '51-100': 0, '100+': 0
    }
    for r in rows:
        n = r[2]
        if n <= 10: buckets['0-10'] += 1
        elif n <= 20: buckets['11-20'] += 1
        elif n <= 30: buckets['21-30'] += 1
        elif n <= 50: buckets['31-50'] += 1
        elif n <= 100: buckets['51-100'] += 1
        else: buckets['100+'] += 1
    
    print(f"\nDistribution of memories per message:")
    for bucket, count in buckets.items():
        bar = '#' * count
        print(f"  {bucket:>6}: {count:>3} msgs {bar}")
