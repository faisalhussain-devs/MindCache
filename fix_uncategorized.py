"""
Fix Uncategorized Memories
==========================
Finds all memories assigned to the "Uncategorized" topic,
sends them to the LLM in batches, and reassigns them to proper chains.

Usage:
    python fix_uncategorized.py
"""

import json
from datetime import datetime
from sqlalchemy import text, func
from sqlalchemy.orm import sessionmaker

from Database.db_setup import engine, Topic, TopicEmbeddingCache
from Database.db_setup import UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Database.db_manager import DatabaseManager
from Memory_extract.summary_extractor import Summary_Extractor

# ─── Config ───
CHARS_PER_TOKEN = 4
TOKEN_BUDGET = 8000
MAX_CHARS = TOKEN_BUDGET * CHARS_PER_TOKEN

SYSTEM_PROMPT = """You are a Topic Chain Generator for a personal memory system.

Given a batch of memories (each with a unique ID), assign EACH memory
its own topic_chain — a hierarchical path like ["Travel", "Bandung", "Dining"].

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
  "143": ["Technology", "Cloud Computing", "AWS"],
  ...
}

Return ONLY valid JSON. No explanations."""


def run():
    Session = sessionmaker(bind=engine)
    session = Session()
    db_manager = DatabaseManager()
    extractor = Summary_Extractor(sys_prompt=SYSTEM_PROMPT)

    try:
        # Find the "Uncategorized" topic node
        uncat = session.query(Topic).filter(func.lower(Topic.name) == "uncategorized").first()
        if not uncat:
            print("[Fix] No 'Uncategorized' topic found. Nothing to do.")
            return

        print(f"[Fix] Found Uncategorized topic (id={uncat.id})")

        # Collect all memories under Uncategorized
        type_map = [
            (UserMemory, "user"),
            (EpisodicMemory, "episodic"),
            (KnowledgeMemory, "knowledge"),
            (DecisionMemory, "decision"),
        ]

        memories = []
        for MemClass, mem_type in type_map:
            for mem in session.query(MemClass).filter(MemClass.topic_id == uncat.id).all():
                memories.append({
                    "id": mem.id,
                    "type": mem_type,
                    "content": mem.content or "",
                    "timestamp": mem.timestamp or datetime.now(),
                })

        print(f"[Fix] Found {len(memories)} uncategorized memories.")
        if not memories:
            return

        # Build batches
        batches = []
        current_batch = []
        current_chars = 0

        for mem in memories:
            line = f"ID={mem['id']} [{mem['type'].upper()}] {mem['content'][:500]}"
            line_chars = len(line)

            if current_chars + line_chars > MAX_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(mem)
            current_chars += line_chars

        if current_batch:
            batches.append(current_batch)

        print(f"[Fix] {len(memories)} memories → {len(batches)} LLM batches.")

        type_class_map = {
            "user": UserMemory,
            "episodic": EpisodicMemory,
            "knowledge": KnowledgeMemory,
            "decision": DecisionMemory,
        }

        total_fixed = 0

        for i, batch in enumerate(batches):
            batch_ids = {m["id"] for m in batch}
            print(f"\n[Fix] Batch {i+1}/{len(batches)} ({len(batch)} memories)...")

            # Build prompt
            lines = []
            for mem in batch:
                lines.append(f"ID={mem['id']} [{mem['type'].upper()}] {mem['content'][:500]}")

            prompt = "\n".join(lines)

            # Add tree hints for grounding
            tree_hints = db_manager.get_topic_tree_hints()
            if tree_hints:
                prompt += f"\n\n### EXISTING TOPICS (reuse these exact names when applicable)\n{tree_hints}"

            prompt += "\n\nAssign a topic_chain to each MEMORY ID. Return JSON only."

            response = extractor.summary_extract(prompt)
            if not response:
                print(f"  [ERROR] No LLM response for batch {i+1}")
                continue

            try:
                clean = response.replace("```json", "").replace("```", "")
                s = clean.find("{")
                e = clean.rfind("}")
                if s != -1 and e != -1:
                    clean = clean[s:e+1]
                data = json.loads(clean)

                batch_fixed = 0
                for mid_str, chain in data.items():
                    mid = int(mid_str) if mid_str.isdigit() else mid_str
                    if mid in batch_ids and isinstance(chain, list) and chain:
                        # Find the memory info
                        mem_info = next(m for m in batch if m["id"] == mid)
                        MemClass = type_class_map[mem_info["type"]]
                        obj = session.get(MemClass, mid)
                        if obj:
                            leaf = db_manager._get_or_create_topic_path(
                                session, chain, job_timestamp=mem_info["timestamp"]
                            )
                            obj.topic_id = leaf.id
                            session.add(obj)
                            batch_fixed += 1

                session.commit()
                total_fixed += batch_fixed
                print(f"  [OK] Fixed {batch_fixed}/{len(batch)} memories.")

            except Exception as ex:
                print(f"  [ERROR] Failed to parse LLM response: {ex}")
                session.rollback()

        # Clean up empty Uncategorized node
        remaining = sum(
            session.query(MemClass).filter(MemClass.topic_id == uncat.id).count()
            for MemClass, _ in type_map
        )
        if remaining == 0:
            session.delete(uncat)
            session.commit()
            print(f"\n[Fix] Deleted empty Uncategorized node.")

        print(f"\n[Fix] Done! Fixed {total_fixed}/{len(memories)} memories.")

    except Exception as ex:
        session.rollback()
        print(f"[Fix] FATAL ERROR: {ex}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run()
