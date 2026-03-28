import json
import sys
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from Database.db_setup import engine, Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Memory_extract.summary_extractor import Summary_Extractor

MAX_CHARS_PER_BATCH = 32000
LEAF_MIN_DESC_CHARS = 160


def _collect_leaf_context(session, node):
    """Grab raw memory text for a leaf node (no JSON merging, just readable text)."""
    models = [
        (EpisodicMemory, "Episodic"),
        (UserMemory, "User"),
        (KnowledgeMemory, "Knowledge"),
    ]
    lines = []
    for Model, label in models:
        for mem in session.query(Model).filter(Model.topic_id == node.id).all():
            if mem.content:
                lines.append(f"[{label}] {mem.content}")

    for dec in session.query(DecisionMemory).filter(DecisionMemory.topic_id == node.id).all():
        if dec.content:
            ctx = f" | Context: {dec.context}" if dec.context else ""
            lines.append(f"[Decision:{dec.status}] {dec.content}{ctx}")

    return "\n".join(lines)


def _collect_parent_context(node):
    """Build context for a parent from its children's names + descriptions."""
    lines = []
    for child in (node.children or []):
        desc = child.description or "(no description yet)"
        lines.append(f"- {child.name}: {desc}")
    return "\n".join(lines)


def _collect_root_context(node):
    """Build a root-only context from child names so the root stays broad."""
    lines = [f"Root name: {node.name}"]
    children = list(node.children or [])
    if children:
        lines.append("Direct child topics:")
        for child in children:
            child_name = (child.name or "").strip()
            if child_name:
                lines.append(f" {child_name}: {child.description}")
    return "\n".join(lines)


def _needs_refresh(node):
    """Leaves refresh when missing/too short; non-leaves rebuild from child descriptions."""
    desc = (node.description or "").strip()
    if not desc:
        return True


def _children_ready(node):
    """Parents can be described only after all children have descriptions."""
    return all((child.description or "").strip() for child in (node.children or []))


def _build_batch_prompt(mode, batch):
    if mode == "leaf":
        intro = (
            "You are generating detailed retrieval descriptions for leaf Topic Nodes inside a memory system.\n"
            "Each leaf node includes raw memory evidence.\n"
            "Generate one dense paragraph per node that covers the full scope of what the leaf contains.\n"
            "Preserve concrete retrieval details when present: people, names, projects, facts, tasks, preferences, dates, places, numbers, outcomes, and decision context.\n"
            "Do not write a tiny generic summary.\n"
            "RETURN ONLY A JSON DICTIONARY mapping the exact Node ID to the new description.\n\n"
        )
        example = '{\n  "12": "Detailed leaf description...",\n  "15": "Detailed leaf description..."\n}'
    elif mode == "root":
        intro = (
            "You are generating detailed retrieval descriptions for ROOT Topic Nodes inside a memory system.\n"
            "Each root node should stay broad and act as a retrieval anchor for its whole subtree.\n"
            "Generate one root description per node as 8-9 short lines.\n"
            "Each line should describe a major theme, common query area, or broad subdomain covered by the root.\n"
            "Use generalized language that helps retrieval, not child-by-child detail.\n"
            "Do not enumerate many specific child facts, names, dates.\n"
            "Cover the full root in a coarse, conceptual way that can match related queries.\n"
            "RETURN ONLY A JSON DICTIONARY mapping the exact Node ID to the new description.\n\n"
        )
        example = '{\n  "1": "Line 1\\nLine 2\\nLine 3\\nLine 4\\nLine 5\\nLine 6\\nLine 7\\nLine 8",\n  "2": "Line 1\\nLine 2\\nLine 3\\nLine 4\\nLine 5\\nLine 6\\nLine 7\\nLine 8\\nLine 9"\n}'
    else:
        intro = (
            "You are generating detailed retrieval descriptions for parent Topic Nodes inside a memory system.\n"
            "Each parent node includes child topic descriptions.\n"
            "Generate one dense paragraph per node that covers the full subtree, not just the topic label.\n"
            "Mention the major child themes and preserve concrete retrieval details when present.\n"
            "Do not write a tiny generic summary.\n"
            "RETURN ONLY A JSON DICTIONARY mapping the exact Node ID to the new description.\n\n"
        )
        example = '{\n  "12": "Detailed parent description...",\n  "15": "Detailed parent description..."\n}'

    nodes_data = {}
    for item in batch:
        nodes_data[str(item["id"])] = {
            "name": item["name"],
            "context": item["context"][:5000]
        }

    return intro + json.dumps(nodes_data, indent=2) + "\n\nExpected output (valid JSON only):\n" + example


def _execute_batch(extractor, session, batch, mode):
    """Send a batch of nodes to the LLM and write descriptions back."""
    prompt = _build_batch_prompt(mode, batch)

    response = extractor.summary_extract(prompt)
    if not response:
        print("  [Batch] Failed to get LLM response.")
        return

    try:
        clean = response.replace("```json", "").replace("```", "")
        s = clean.find("{")
        e = clean.rfind("}")
        if s != -1 and e != -1:
            clean = clean[s:e+1]
        data = json.loads(clean)

        applied = 0
        for item in batch:
            nid = str(item["id"])
            if nid in data:
                new_desc = str(data[nid]).strip()
                old_desc = (item["node"].description or "").strip()
                if new_desc and new_desc != old_desc:
                    item["node"].description = new_desc
                    item["node"].embedding = None
                    item["node"].timestamp = datetime.now()
                    session.add(item["node"])
                    applied += 1
            else:
                print(f"  [Batch] Warning: ID {nid} ({item['name']}) missing from LLM response")

        session.commit()
        print(f"  [Batch] Applied {applied}/{len(batch)} descriptions.")
    except Exception as ex:
        print(f"  [Batch] Error parsing response: {ex}")


def run(with_embeddings=True, refresh_roots=True):
    Session = sessionmaker(bind=engine)
    session = Session()
    extractor = Summary_Extractor()

    try:
        max_depth = session.query(func.max(Topic.level)).scalar() or 0
        print(f"[DescOnly] Max Depth: {max_depth}. Processing leaves first, then parents bottom-up.")

        leaf_pending = []
        for node in session.query(Topic).all():
            if node.children or not _needs_refresh(node):
                continue
            ctx = _collect_leaf_context(session, node)
            if ctx:
                leaf_pending.append({
                    "node": node,
                    "id": node.id,
                    "name": node.name,
                    "context": ctx
                })

        if leaf_pending:
            _send_in_batches(extractor, session, leaf_pending, "All Leaves", "leaf")
            session.expire_all()
        
        c = 1

        while c != 0:
            c = 0
            parent_pending = []
            for level in range(max_depth, -1, -1):
                for node in session.query(Topic).filter(Topic.level == level).all():
                    child_ready =  _children_ready(node)
                    if not child_ready:
                        c = 1
                    needs_refresh = _needs_refresh(node)
                    if level == 0 and refresh_roots:
                        needs_refresh = True
                    if not node.children or not needs_refresh or not child_ready:
                        continue

                    ctx = _collect_root_context(node) if level == 0 else _collect_parent_context(node)
                    if ctx:
                        parent_pending.append({
                            "node": node,
                            "id": node.id,
                            "name": node.name,
                            "context": ctx
                        })

            if parent_pending:
                mode = "root" if level == 0 else "parent"
                _send_in_batches(extractor, session, parent_pending, f"Level {level} Parents", mode)
                session.expire_all()

        print("[DescOnly] Done.")

        if with_embeddings:
            print("\n[DescOnly] Running embedding job...")
            run_embedding_job()

    finally:
        session.close()


def _send_in_batches(extractor, session, pending, label, mode):
    """Split pending items into size-limited batches and execute."""
    batches = []
    current_batch = []
    current_len = 0

    for item in pending:
        est = len(item["name"]) + len(item["context"]) + 100
        if current_len + est > MAX_CHARS_PER_BATCH and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_len = 0
        current_batch.append(item)
        current_len += est

    if current_batch:
        batches.append(current_batch)

    print(f"  [{label}] {len(pending)} nodes -> {len(batches)} batches")
    for i, batch in enumerate(batches):
        print(f"  [{label}] Batch {i+1}/{len(batches)} ({len(batch)} nodes)...")
        _execute_batch(extractor, session, batch, mode)


if __name__ == "__main__":
    run(
        with_embeddings="--with-embeddings" in sys.argv,
        refresh_roots="--refresh-roots" in sys.argv,
    )
