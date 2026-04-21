"""
migrate_summary_timestamps.py
==============================
Sets Topic.mem_start and Topic.mem_end for every node bottom-up,
derived from actual memory table timestamps.

Also cleans up any time_start/time_end fields that were previously
injected into parent summary JSON (old approach, now replaced by columns).
Source_map entries that were converted to dicts are converted back to
plain strings.

Run:
    python migrate_summary_timestamps.py
    python migrate_summary_timestamps.py --dry-run
"""
import json
import sys
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from Database.db_setup import (
    engine, Topic,
    EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory
)

DRY_RUN = "--dry-run" in sys.argv
FMT = "%Y-%m-%d %H:%M"
Session = sessionmaker(bind=engine)


def get_leaf_time_range(session, topic_id: int) -> tuple[datetime | None, datetime | None]:
    all_dts = []
    for Model in (EpisodicMemory, UserMemory, KnowledgeMemory):
        for (dt,) in session.query(Model.timestamp).filter(Model.topic_id == topic_id).all():
            if dt:
                all_dts.append(dt)
    for d in session.query(DecisionMemory).filter(DecisionMemory.topic_id == topic_id).all():
        # Use timestamp (when decision was created), NOT last_validated_at
        # last_validated_at updates every time the decision_analyzer runs — it's a
        # pipeline artifact, not a real memory event, and inflates mem_end incorrectly.
        dt = d.timestamp
        if dt:
            all_dts.append(dt)
    # Fallback: parse timestamps from existing summary JSON keys
    if not all_dts:
        topic = session.get(Topic, topic_id)
        if topic and topic.summary:
            try:
                data = json.loads(topic.summary)
                for k in list(data.get("memories", {}).keys()) + list(data.get("decisions", {}).keys()):
                    try:
                        all_dts.append(datetime.strptime(k, FMT))
                    except ValueError:
                        pass
            except Exception:
                pass
    return (min(all_dts), max(all_dts)) if all_dts else (None, None)


def clean_parent_json(node: Topic):
    """
    Remove time_start/time_end from parent summary JSON.
    Convert any source_map entries from {text,t0,t1} dicts back to plain strings.
    """
    if not node.summary:
        return
    try:
        data = json.loads(node.summary)
    except Exception:
        return

    changed = False

    # Remove injected time fields
    for key in ("time_start", "time_end"):
        if key in data:
            del data[key]
            changed = True

    # Revert source_map dict entries back to plain strings
    source_map = data.get("source_map", {})
    clean_map = {}
    for cid_str, entry in source_map.items():
        if isinstance(entry, dict):
            clean_map[cid_str] = entry.get("text", "")
            changed = True
        else:
            clean_map[cid_str] = entry
    data["source_map"] = clean_map

    if changed and not DRY_RUN:
        node.summary = json.dumps(data)


def run():
    session = Session()
    max_depth = session.query(func.max(Topic.level)).scalar() or 0
    print(f"[Migration] Max depth: {max_depth}  |  DRY_RUN={DRY_RUN}")

    # ── Pass 1: Leaves (nodes with no children, at any level) ──────────────
    print(f"\n=== Pass 1: Leaves (real leaves, any level) ===")

    # Find all leaf node IDs: topics that are not a parent_id of any other topic
    from sqlalchemy import not_, exists
    leaf_nodes = (
        session.query(Topic)
        .filter(not_(exists().where(Topic.parent_id == Topic.id)))
        .all()
    )
    print(f"  Found {len(leaf_nodes)} leaf nodes")
    for node in leaf_nodes:
        t0, t1 = get_leaf_time_range(session, node.id)
        print(f"  [{node.id}] {node.name[:40]:<40}  t0={t0}  t1={t1}")
        if not DRY_RUN:
            node.mem_start = t0
            node.mem_end   = t1
            session.add(node)
    if not DRY_RUN:
        session.commit()
        print(f"  Committed {len(leaf_nodes)} leaves.")

    # ── Pass 2: Parents (bottom-up) ──────────────────────────────────────
    for level in range(max_depth - 1, -1, -1):
        nodes = session.query(Topic).filter(Topic.level == level).all()
        parents = [n for n in nodes
                   if session.query(Topic.id).filter(Topic.parent_id == n.id).first()]
        if not parents:
            continue

        print(f"\n=== Level {level}: {len(parents)} parent nodes ===")
        for node in parents:
            children = session.query(Topic).filter(Topic.parent_id == node.id).all()

            child_starts = [c.mem_start for c in children if c.mem_start]
            child_ends   = [c.mem_end   for c in children if c.mem_end]
            t0 = min(child_starts) if child_starts else None
            t1 = max(child_ends)   if child_ends   else None

            print(f"  [{node.id}] {node.name[:40]:<40}  t0={t0}  t1={t1}")
            clean_parent_json(node)  # Repair any JSON pollution from previous migration

            if not DRY_RUN:
                node.mem_start = t0
                node.mem_end   = t1
                session.add(node)

        if not DRY_RUN:
            session.commit()
            print(f"  Committed level {level}.")

    session.close()
    print("\n[Migration] Done.")
    if DRY_RUN:
        print("[Migration] DRY RUN — nothing written.")


if __name__ == "__main__":
    run()
