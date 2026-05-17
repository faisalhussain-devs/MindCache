"""Full DB health audit: ghost nodes, source_map bloat, description sizes."""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic

s = sessionmaker(bind=engine)()
all_topics = s.query(Topic).all()
all_valid_ids = set(str(t.id) for t in all_topics)
topic_by_id = {t.id: t for t in all_topics}

parents = [t for t in all_topics if t.children]
leaves  = [t for t in all_topics if not t.children]

print("=" * 70)
print("1. GHOST NODE AUDIT (source_map/ignored_ids referencing deleted nodes)")
print("=" * 70)
ghost_count = 0
for p in parents:
    if not p.summary or not p.summary.startswith("{"):
        continue
    try:
        state = json.loads(p.summary)
    except:
        continue
    sm_ids = set(state.get("source_map", {}).keys())
    ig_ids = set(str(x) for x in state.get("ignored_ids", []))
    all_ids = sm_ids | ig_ids
    actual_child_ids = set(str(c.id) for c in p.children)

    # Ghost = in map but not a real child
    ghost_sm  = sm_ids - actual_child_ids
    ghost_ig  = ig_ids - actual_child_ids
    ghost_any = ghost_sm | ghost_ig

    if ghost_any:
        ghost_count += 1
        deleted = [x for x in ghost_any if x not in all_valid_ids]
        wrong   = [x for x in ghost_any if x in all_valid_ids]
        print(f"  '{p.name}' (id={p.id}): {len(ghost_sm)} ghost in source_map, {len(ghost_ig)} ghost in ignored")
        if deleted: print(f"    DELETED IDs: {sorted(deleted, key=int)}")
        if wrong:   print(f"    WRONG (live but not child): {sorted(wrong, key=int)}")

print(f"\n  Total parents with ghost IDs: {ghost_count}")

print()
print("=" * 70)
print("2. SOURCE_MAP COUNT VS ACTUAL CHILDREN")
print("=" * 70)
over_count = 0
for p in parents:
    if not p.summary or not p.summary.startswith("{"):
        continue
    try:
        state = json.loads(p.summary)
    except:
        continue
    sm_ids = set(state.get("source_map", {}).keys())
    ig_ids = set(str(x) for x in state.get("ignored_ids", []))
    actual = len(p.children)
    total_mapped = len(sm_ids) + len(ig_ids)
    if total_mapped > actual:
        over_count += 1
        print(f"  '{p.name}' (id={p.id}): {actual} children, but {len(sm_ids)} in map + {len(ig_ids)} ignored = {total_mapped}")

if over_count == 0:
    print("  All parents: source_map + ignored_ids <= actual children count ✅")
print(f"\n  Total over-mapped: {over_count}")

print()
print("=" * 70)
print("3. DESCRIPTION SIZE AUDIT")
print("=" * 70)

print("\n  -- BLOATED LEAVES (desc > 6000 chars, ~1500 tokens) --")
bloated_leaves = 0
for t in sorted(leaves, key=lambda x: len(x.description or ''), reverse=True):
    d = len(t.description or '')
    mems = len(t.episodic_memories) + len(t.user_memories) + len(t.knowledge_memories) + len(t.decision_memories)
    if d > 6000:
        bloated_leaves += 1
        print(f"  '{t.name}' (id={t.id}): {d} chars (~{d//4} tok), {mems} memories")
print(f"  Total: {bloated_leaves} bloated leaves")

print("\n  -- BLOATED PARENTS (desc > 10000 chars, ~2500 tokens) --")
bloated_parents = 0
for t in sorted(parents, key=lambda x: len(x.description or ''), reverse=True):
    d = len(t.description or '')
    if d > 10000:
        bloated_parents += 1
        print(f"  '{t.name}' (id={t.id}): {d} chars (~{d//4} tok), {len(t.children)} children")
print(f"  Total: {bloated_parents} bloated parents")

print("\n  -- EMPTY DESCRIPTIONS (parents with no description) --")
empty_parents = 0
for t in parents:
    d = len((t.description or '').strip())
    if d < 50:
        empty_parents += 1
        print(f"  '{t.name}' (id={t.id}): {len(t.children)} children, desc={d} chars")
print(f"  Total: {empty_parents} empty parents")

print()
print("=" * 70)
print("4. SUMMARY STATISTICS")
print("=" * 70)
leaf_descs  = [len(t.description or '') for t in leaves]
par_descs   = [len(t.description or '') for t in parents]
leaf_mems   = [len(t.episodic_memories)+len(t.user_memories)+len(t.knowledge_memories)+len(t.decision_memories) for t in leaves]

def stats(vals, label):
    if not vals: return
    print(f"  {label}: min={min(vals)}, avg={sum(vals)//len(vals)}, max={max(vals)}, total={len(vals)}")

stats(leaf_descs,  "Leaf desc chars")
stats(par_descs,   "Parent desc chars")
stats(leaf_mems,   "Leaf memory counts")
print(f"  Total nodes: {len(all_topics)} ({len(leaves)} leaves, {len(parents)} parents)")

s.close()
