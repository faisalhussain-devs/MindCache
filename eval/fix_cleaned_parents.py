"""
Fix descriptions for parents whose source_maps were cleaned:
- Parents with source_map entries: rebuild description = join(source_map.values())
- Parents with empty source_map: run RecursiveSummarizer for those nodes
"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic
from Database.nodes_summary import RecursiveSummarizer

Session = sessionmaker(bind=engine)
s = Session()

# The 11 parents that were cleaned
CLEANED_IDS = [1, 738, 895, 961, 1026, 1027, 1041, 1054, 1062, 1063, 1035]

print("Phase 1: Rebuild descriptions from clean source_maps")
print("=" * 60)
needs_summarization = []

for tid in CLEANED_IDS:
    node = s.get(Topic, tid)
    if not node:
        print(f"  ID {tid}: NOT FOUND")
        continue

    # Parse current source_map
    source_map = {}
    try:
        if node.summary and node.summary.startswith("{"):
            state = json.loads(node.summary)
            source_map = state.get("source_map", {})
    except:
        pass

    if source_map:
        # Rebuild description from clean source_map values
        new_desc = "\n".join(source_map.values())
        old_len = len(node.description or "")
        node.description = new_desc
        node.embedding = None  # Force re-embedding
        s.add(node)
        print(f"  '{node.name}' (id={tid}): rebuilt desc {old_len} -> {len(new_desc)} chars from {len(source_map)} source entries")
    else:
        # Empty source_map - needs LLM re-summarization
        needs_summarization.append(tid)
        print(f"  '{node.name}' (id={tid}): EMPTY source_map - queued for LLM summarization")

s.commit()
print(f"\n  Phase 1 done. {len(CLEANED_IDS) - len(needs_summarization)} descriptions rebuilt.")

print()
print("Phase 2: LLM re-summarization for empty parents")
print("=" * 60)

# Also add the 5 known-empty parents
empty_ids = set(needs_summarization)
for t in s.query(Topic).all():
    if t.children and len((t.description or '').strip()) < 50:
        empty_ids.add(t.id)

print(f"Nodes needing LLM summarization: {sorted(empty_ids)}")
print()

if empty_ids:
    summarizer = RecursiveSummarizer()
    for tid in sorted(empty_ids):
        node = s.get(Topic, tid)
        if not node:
            continue
        print(f"\nProcessing '{node.name}' (id={tid}), {len(node.children)} children...")
        if not node.children:
            summarizer.process_leaf(s, node)
        else:
            summarizer.process_parent(s, node)
        s.commit()
        new_desc = node.description or ""
        print(f"  -> Description now: {len(new_desc)} chars (~{len(new_desc)//4} tok)")

print("\nAll done!")
s.close()
