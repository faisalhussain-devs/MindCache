import json
import sys
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from Database.db_setup import engine, Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Memory_extract.summary_extractor import Summary_Extractor

MAX_CHARS_PER_BATCH = 32000


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


def _execute_batch(extractor, session, batch):
    """Send a batch of nodes to the LLM and write descriptions back."""
    prompt = (
        "You are generating concise, keyword-rich descriptions for Topic Nodes inside a memory system.\n"
        "Each node has a name and context (its memories or child topics).\n"
        "Generate a short description suitable for semantic retrieval embedding.\n"
        "RETURN ONLY A JSON DICTIONARY mapping the exact Node ID to the new description.\n\n"
    )

    nodes_data = {}
    for item in batch:
        nodes_data[str(item["id"])] = {
            "name": item["name"],
            "context": item["context"][:5000]
        }

    prompt += json.dumps(nodes_data, indent=2)
    prompt += '\n\nExpected output (valid JSON only):\n'
    prompt += '{\n  "12": "concise description...",\n  "15": "concise description..."\n}'

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
                item["node"].description = data[nid]
                item["node"].timestamp = datetime.now()
                session.add(item["node"])
                applied += 1
            else:
                print(f"  [Batch] Warning: ID {nid} ({item['name']}) missing from LLM response")

        session.commit()
        print(f"  [Batch] Applied {applied}/{len(batch)} descriptions.")
    except Exception as ex:
        print(f"  [Batch] Error parsing response: {ex}")


def run(with_embeddings=True):
    Session = sessionmaker(bind=engine)
    session = Session()
    extractor = Summary_Extractor()

    try:
        # ── Phase 1: Leaves (no children) ──
        max_depth = session.query(func.max(Topic.level)).scalar() or 0
        leaves = session.query(Topic).filter(
            Topic.description == None
        ).all()
        
        leaf_nodes = [n for n in leaves if not n.children]
        parent_nodes = [n for n in leaves if n.children]

        print(f"[DescOnly] {len(leaf_nodes)} leaves, {len(parent_nodes)} parents need descriptions.")

        # Process leaves
        if leaf_nodes:
            pending = []
            for node in leaf_nodes:
                ctx = _collect_leaf_context(session, node)
                if ctx:
                    pending.append({
                        "node": node,
                        "id": node.id,
                        "name": node.name,
                        "context": ctx
                    })

            if pending:
                _send_in_batches(extractor, session, pending, "Leaves")

        # ── Phase 2: Parents (process after leaves so child descriptions exist) ──
        # Re-query parents since some children may now have descriptions
        if parent_nodes:
            pending = []
            for node in parent_nodes:
                ctx = _collect_parent_context(node)
                if ctx:
                    pending.append({
                        "node": node,
                        "id": node.id,
                        "name": node.name,
                        "context": ctx
                    })

            if pending:
                _send_in_batches(extractor, session, pending, "Parents")

        print("[DescOnly] Done.")

        # ── Optional: run embedding job ──
        if with_embeddings:
            print("\n[DescOnly] Running embedding job...")
            from Database.embedder import run_embedding_job
            run_embedding_job()

    finally:
        session.close()


def _send_in_batches(extractor, session, pending, label):
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

    print(f"  [{label}] {len(pending)} nodes → {len(batches)} batches")
    for i, batch in enumerate(batches):
        print(f"  [{label}] Batch {i+1}/{len(batches)} ({len(batch)} nodes)...")
        _execute_batch(extractor, session, batch)


if __name__ == "__main__":
    run(with_embeddings="--with-embeddings" in sys.argv)
