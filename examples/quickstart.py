"""
MindCache Quickstart

A minimal example:
initialize → add → process → inspect → search
"""

import os
import sys

sys.path.insert(
0,
os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from mindcache import MindCache
from mindcache.utils.pretty import (
pretty_print_memories,
pretty_print_tree,
pretty_print_context,
)

def main():
    db_path = "quickstart_demo.db"
    # Start with a clean demo database.
    if os.path.exists(db_path):
        os.remove(db_path)

    print("Initializing MindCache...")

    mc = MindCache(
        db_path=db_path,
        provider="gemini",
        model_name="gemini-2.5-flash",
    )

    # Add a conversation.
    print("\nAdding conversation...")

    mc.add(
        [
            {
                "role": "user",
                "content": (
                    "I prefer Python and FastAPI for backend development, "
                    "and PostgreSQL for databases."
                ),
            },
            {
                "role": "assistant",
                "content": "Got it. I'll remember that.",
            },
        ],
        user_id="alice",
    )

    # Process the conversation into memories.
    print("Processing...")

    try:
        mc.process(user_id="alice")
    except Exception as e:
        print(f"Processing failed: {e}")
        print("Make sure GEMINI_API_KEY is configured.")
        return

    # Inspect memories.
    memories = mc.inspect(
        user_id="alice",
        view="memories",
    )

    pretty_print_memories(memories)

    # Inspect the topic tree.
    tree = mc.inspect(
        user_id="alice",
        view="tree",
    )

    pretty_print_tree(tree)

    # Search memories.
    query = "What database does Alice prefer?"

    print(f"\nSearching: {query}")

    context = mc.search(
        query,
        user_id="alice",
    )

    pretty_print_context(
        context,
        query=query,
    )

if __name__ == "__main__":
    main()
