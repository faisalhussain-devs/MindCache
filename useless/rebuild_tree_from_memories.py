import json
import sys
import os
import shutil
from datetime import datetime
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, TopicEmbeddingCache, TriadBlock
from Database.db_setup import UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Database.db_manager import DatabaseManager
from Memory_extract.summary_extractor import Summary_Extractor

# ─── Config ───
CHARS_PER_TOKEN = 4
TOKEN_BUDGET = 8000
MAX_CHARS = TOKEN_BUDGET * CHARS_PER_TOKEN  # 32000 chars ≈ 8000 tokens
DB_PATH = "mindcache.db"

SYSTEM_PROMPT = """You are a Topic Chain Generator for a personal memory system.

Given a batch of extracted memories (each with a unique ID), assign EACH memory
its own topic_chain — a hierarchical path like ["Travel", "Bandung", "Dining"].

Memories are grouped by their source message for context, but each memory
can have a DIFFERENT chain if it covers a different topic.

### CHAIN RULES
1. The chain represents a CONCEPTUAL path, not a structural taxonomy.
   - "Nasi Goreng at a restaurant in Bandung" → ["Travel", "Bandung", "Dining"] NOT ["Food & Recipes", "Indonesian Cuisine"]
   - The chain must reflect the CONTEXT in which the information was discussed.
2. Root (first item) should be a broad domain: Travel, Technology, Health, etc.
3. Chain length: Each level adds meaningful specificity. Make it as long as required.
4. Memories about the same sub-topic CAN share a chain. Different sub-topics get different chains.

### OUTPUT FORMAT
Return a JSON object mapping each MEMORY ID to its chain:
{
  "142": ["Travel", "Bandung", "Dining"],
  "143": ["Travel", "Bandung", "Shopping"],
  "150": ["Technology", "Cloud Computing", "AWS"],
  ...
}

Return ONLY valid JSON. No explanations."""


def _collect_memories_by_message(session):
    """
    Group all memories by their message_id (TriadBlock).
    Returns: { message_id: { "timestamp": dt, "memories": [{"id": .., "type": .., "content": ..}, ...] } }
    """
    groups = defaultdict(lambda: {"timestamp": None, "memories": []})

    type_map = [
        (UserMemory, "user"),
        (EpisodicMemory, "episodic"),
        (KnowledgeMemory, "knowledge"),
        (DecisionMemory, "decision"),
    ]

    for MemClass, mem_type in type_map:
        for mem in session.query(MemClass).all():
            mid = mem.message_id
            if mid is None:
                continue
            groups[mid]["memories"].append({
                "id": mem.id,
                "type": mem_type,
                "content": mem.content or "",
            })
            if groups[mid]["timestamp"] is None and mem.timestamp:
                groups[mid]["timestamp"] = mem.timestamp

    return dict(groups)


def _format_group(group_id, group_data):
    """Format a single message group into prompt text, showing memory IDs."""
    lines = [f"[MESSAGE GROUP {group_id}]"]
    for mem in group_data["memories"]:
        lines.append(f"  ID={mem['id']} [{mem['type'].upper()}] {mem['content'][:500]}")
    return "\n".join(lines)


def _build_batches(groups):
    """
    Batch message groups into LLM-call-sized chunks.
    Each batch stays under MAX_CHARS.
    """
    batches = []
    current_batch = {}  # group_id -> group_data
    current_chars = 0

    for gid, gdata in groups.items():
        group_text = _format_group(gid, gdata)
        group_chars = len(group_text)

        if current_chars + group_chars > MAX_CHARS and current_batch:
            batches.append(current_batch)
            current_batch = {}
            current_chars = 0

        current_batch[gid] = gdata
        current_chars += group_chars

    if current_batch:
        batches.append(current_batch)

    return batches


def _nuke_topics(session, dry_run=True):
    """Unlink all memories from topics, then delete all topics and caches."""
    if dry_run:
        topic_count = session.query(Topic).count()
        print(f"  [DRY-RUN] Would delete {topic_count} topics and unlink all memories.")
        return

    print("  Unlinking memories from topics...")
    session.execute(text("UPDATE memories_user SET topic_id = NULL"))
    session.execute(text("UPDATE memories_episodic SET topic_id = NULL"))
    session.execute(text("UPDATE memories_knowledge SET topic_id = NULL"))
    session.execute(text("UPDATE memories_decision SET topic_id = NULL"))

    print("  Deleting topic embedding cache...")
    session.query(TopicEmbeddingCache).delete()

    print("  Deleting all topics...")
    # Delete leaf-first to avoid FK issues (children before parents)
    while True:
        # Find topics that have no children
        leaf_topics = session.query(Topic).filter(
            ~Topic.id.in_(
                session.query(Topic.parent_id).filter(Topic.parent_id != None)
            )
        ).all()
        if not leaf_topics:
            break
        for t in leaf_topics:
            session.delete(t)
        session.flush()

    session.commit()
    remaining = session.query(Topic).count()
    print(f"  Topics remaining after nuke: {remaining}")


# Build a flat lookup: memory_id -> {type, timestamp}
def _build_memory_lookup(groups):
    """Create a flat dict: memory_id -> {type, timestamp} for reassignment."""
    lookup = {}
    for gid, gdata in groups.items():
        ts = gdata.get("timestamp") or datetime.now()
        for mem in gdata["memories"]:
            lookup[mem["id"]] = {"type": mem["type"], "timestamp": ts}
    return lookup


def _reassign_memory(session, db_manager, mem_id, mem_info, chain):
    """
    Given a chain like ["Travel", "Bandung", "Food"], create the topic path
    and reassign a single memory to the leaf node.
    """
    type_class_map = {
        "user": UserMemory,
        "episodic": EpisodicMemory,
        "knowledge": KnowledgeMemory,
        "decision": DecisionMemory,
    }
    MemClass = type_class_map[mem_info["type"]]
    obj = session.get(MemClass, mem_id)
    if obj:
        leaf_node = db_manager._get_or_create_topic_path(session, chain, job_timestamp=mem_info["timestamp"])
        obj.topic_id = leaf_node.id
        session.add(obj)


def run(dry_run=True):
    Session = sessionmaker(bind=engine)
    session = Session()
    db_manager = DatabaseManager()
    extractor = Summary_Extractor(sys_prompt=SYSTEM_PROMPT)

    try:
        # ── Step 1: Backup ──
        if not dry_run and os.path.exists(DB_PATH):
            backup = f"mindcache_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            shutil.copy2(DB_PATH, backup)
            print(f"[Rebuild] Backup created: {backup}")

        # ── Step 2: Collect all memories grouped by message ──
        print("[Rebuild] Collecting memories by message group...")
        groups = _collect_memories_by_message(session)
        total_memories = sum(len(g["memories"]) for g in groups.values())
        print(f"  Found {len(groups)} message groups, {total_memories} total memories.")

        if not groups:
            print("[Rebuild] No memories found. Nothing to do.")
            return

        # ── Step 3: Nuke the topic tree ──
        print("[Rebuild] Nuking topic tree...")
        _nuke_topics(session, dry_run=dry_run)

        # ── Step 4: Batch and send to LLM ──
        batches = _build_batches(groups)
        print(f"[Rebuild] {len(groups)} groups → {len(batches)} LLM batches.")

        all_chains = {}  # memory_id -> chain (for dry-run preview only)
        memory_lookup = _build_memory_lookup(groups)
        total_applied = 0

        for i, batch in enumerate(batches):
            batch_mem_ids = set()
            for gdata in batch.values():
                for mem in gdata["memories"]:
                    batch_mem_ids.add(mem["id"])

            print(f"\n[Rebuild] Batch {i+1}/{len(batches)} ({len(batch)} groups, {len(batch_mem_ids)} memories)...")

            # Build prompt
            prompt_parts = []
            for gid, gdata in batch.items():
                prompt_parts.append(_format_group(gid, gdata))

            prompt = "\n\n".join(prompt_parts)
            prompt += "\n\nAssign a topic_chain to each MEMORY ID. Return JSON only."

            if dry_run:
                print(f"  [DRY-RUN] Would send {len(prompt)} chars ({len(prompt)//CHARS_PER_TOKEN} est. tokens)")
                for mid in batch_mem_ids:
                    all_chains[mid] = ["Uncategorized"]
                continue

            # Push existing tree hints into prompt for grounding
            tree_hints = db_manager.get_topic_tree_hints()
            if tree_hints:
                prompt += f"\n\n### EXISTING TOPICS (reuse these exact names when applicable)\n{tree_hints}"

            response = extractor.summary_extract(prompt)
            if not response:
                print(f"  [ERROR] No LLM response for batch {i+1}")
                for mid in batch_mem_ids:
                    if mid in memory_lookup:
                        _reassign_memory(session, db_manager, mid, memory_lookup[mid], ["Uncategorized"])
                session.commit()
                continue

            try:
                clean = response.replace("```json", "").replace("```", "")
                s = clean.find("{")
                e = clean.rfind("}")
                if s != -1 and e != -1:
                    clean = clean[s:e+1]
                data = json.loads(clean)

                batch_applied = 0
                for mid_str, chain in data.items():
                    mid = int(mid_str) if mid_str.isdigit() else mid_str
                    if mid in batch_mem_ids and isinstance(chain, list) and chain:
                        if mid in memory_lookup:
                            _reassign_memory(session, db_manager, mid, memory_lookup[mid], chain)
                            batch_applied += 1
                    else:
                        print(f"  [WARN] Memory {mid_str} not in batch or invalid chain: {chain}")

                # Any memories missing from LLM response → Uncategorized
                for mid in batch_mem_ids:
                    mid_str = str(mid)
                    if mid_str not in data and mid not in data:
                        if mid in memory_lookup:
                            _reassign_memory(session, db_manager, mid, memory_lookup[mid], ["Uncategorized"])
                        print(f"  [WARN] Memory {mid} missing from LLM response, assigned to Uncategorized")

                # Commit after each batch — crash-safe + enables grounding
                session.commit()
                total_applied += batch_applied
                print(f"  [OK] Committed {batch_applied}/{len(batch_mem_ids)} memories.")

            except Exception as ex:
                print(f"  [ERROR] Failed to parse LLM response: {ex}")
                for mid in batch_mem_ids:
                    if mid in memory_lookup:
                        _reassign_memory(session, db_manager, mid, memory_lookup[mid], ["Uncategorized"])
                session.commit()

        # ── Final stats ──
        if dry_run:
            print(f"\n  [DRY-RUN] Would reassign memories. Showing first 15 chains:")
            for j, (mid, chain) in enumerate(list(all_chains.items())[:15]):
                print(f"    Memory {mid} → {chain}")
            print(f"\n[Rebuild] DRY-RUN complete. Pass --apply to execute.")
            return

        print(f"\n[Rebuild] Tree rebuilt successfully! {total_applied} memories assigned.")
        root_count = session.query(Topic).filter(Topic.parent_id == None).count()
        total_topics = session.query(Topic).count()
        print(f"  New tree: {root_count} roots, {total_topics} total topics.")

    except Exception as ex:
        session.rollback()
        print(f"[Rebuild] FATAL ERROR: {ex}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    is_dry = "--apply" not in sys.argv
    if is_dry:
        print("=" * 60)
        print(" DRY-RUN MODE — No DB changes. Pass --apply to execute.")
        print("=" * 60)
    else:
        print("=" * 60)
        print(" APPLY MODE — Will modify database!")
        print("=" * 60)
    run(dry_run=False)
