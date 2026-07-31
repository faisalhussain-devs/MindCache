"""
view_topics.py  –  Render the full MindCache topic tree to output.txt
Usage:
    python view_topics.py [--file output.txt]
"""
import sys
import os
import argparse
from collections import defaultdict
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.db_setup import engine, Topic


def build_tree_text(session, show_memories=True):
    """Build full tree text for all topics."""
    topics = session.query(Topic).order_by(Topic.level, Topic.id).all()

    node_map = {t.id: t for t in topics}
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)

    # Sort children alphabetically for readability
    for pid in children_map:
        children_map[pid].sort(key=lambda x: x.name.lower())

    # Pre-compute memory counts
    mem_count_map = {}
    for t in topics:
        mem_count_map[t.id] = (
            len(t.episodic_memories) +
            len(t.user_memories) +
            len(t.knowledge_memories) +
            len(t.decision_memories)
        )

    roots = [t for t in topics if t.parent_id is None]
    roots.sort(key=lambda x: x.name.lower())

    lines = []
    total_nodes = len(topics)
    total_leaves = sum(1 for t in topics if not children_map.get(t.id))
    total_memories = sum(mem_count_map.values())

    lines.append("=" * 70)
    lines.append("MINDCACHE TOPIC TREE")
    lines.append("=" * 70)
    lines.append(f"Total nodes  : {total_nodes}")
    lines.append(f"Total roots  : {len(roots)}")
    lines.append(f"Total leaves : {total_leaves}")
    lines.append(f"Total memories: {total_memories}")
    lines.append("=" * 70)
    lines.append("")

    def render_node(node, prefix="", is_last=True, is_root=False):
        children = children_map.get(node.id, [])
        is_leaf = len(children) == 0
        mem_count = mem_count_map.get(node.id, 0)

        if is_leaf:
            tag = f"[memories: {mem_count}]" if show_memories else "[leaf]"
        else:
            tag = ""

        if is_root:
            connector = ""
            display_prefix = prefix
            child_prefix = prefix
        else:
            connector = "└── " if is_last else "├── "
            display_prefix = prefix + connector
            child_prefix = prefix + ("    " if is_last else "│   ")

        lines.append(f"{display_prefix}{node.name} {tag}")

        for idx, child in enumerate(children):
            render_node(child, child_prefix, is_last=(idx == len(children) - 1), is_root=False)

    for root in roots:
        render_node(root, prefix="", is_last=True, is_root=True)
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="View the MindCache topic tree")
    parser.add_argument("--file", default="output.txt", help="Output file path (default: output.txt)")
    parser.add_argument("--no-memories", action="store_true", help="Don't show memory counts on leaves")
    args = parser.parse_args()

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        print(f"Loading topics from database...")
        text = build_tree_text(session, show_memories=not args.no_memories)

        out_path = args.file
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)

        lines = text.count("\n")
        print(f"Tree written to: {os.path.abspath(out_path)}")
        print(f"Lines: {lines}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
