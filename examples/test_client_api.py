import json
import logging
import os
from pathlib import Path

from mindcache import MindCache
from mindcache.utils.pretty import (
    pretty_print_memories,
    pretty_print_tree,
    pretty_print_context,
)

DB_PATH = "mindcache_test.db"
USER_ID = "Faisal"
TEST_DATA_PATH = Path(r"E:\Mind_Cache\test_memory.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(
            "mindcache_test.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)

log = logging.getLogger("mindcache.test")


def load_conversations(path):
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    conversations = []
    current = []

    for turn in data:
        current.append(turn)

        if turn.get("role") == "assistant":
            conversations.append(current)
            current = []

    if current:
        conversations.append(current)

    return conversations


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    mc = MindCache(
        db_path=DB_PATH,
        provider="gemini",
        model_name="gemini-2.5-flash",
        enable_summarization=False,
    )

    log.info("MindCache initialized.")

    conversations = load_conversations(TEST_DATA_PATH)
    log.info("Loaded %d conversations.", len(conversations))

    log.info("Adding conversations...")

    job_ids = []

    for conversation in conversations:
        job_id = mc.add(
            conversation,
            user_id=USER_ID,
        )
        job_ids.append(job_id)

    log.info("Added %d conversations.", len(job_ids))
    log.info("Job IDs: %s", job_ids)

    log.info("Processing queue...")

    result = mc.process(user_id=USER_ID)

    log.info("Processing complete.")
    log.info("Process result: %s", result)

    log.info("Inspecting memories...")

    memories = mc.inspect(
        user_id=USER_ID,
        view="memories",
    )

    if not memories:
        raise RuntimeError("No memories were created.")

    pretty_print_memories(memories)

    log.info("Checking memory type filters...")

    for memory_type in (
        "user",
        "knowledge",
        "episodic",
        "decision",
    ):
        filtered = mc.inspect(
            user_id=USER_ID,
            view="memories",
            memory_type=memory_type,
        )

        log.info(
            "%s: %d memories",
            memory_type,
            len(filtered),
        )

    log.info("Inspecting topic tree...")

    tree = mc.inspect(
        user_id=USER_ID,
        view="tree",
    )

    if tree is None:
        raise RuntimeError("Topic tree was not returned.")

    pretty_print_tree(tree)

    log.info("Inspecting complete state...")

    state = mc.inspect(
        user_id=USER_ID,
        view="all",
    )

    if "memories" not in state or "tree" not in state:
        raise RuntimeError(
            "inspect(view='all') returned an invalid state."
        )

    log.info(
        "Complete state contains %d memories.",
        len(state["memories"]),
    )

    pretty_print_tree(state["tree"])

    queries = [
        "Where does James live?",
        "What are Faisal's preferences?",
        "What decisions were made?",
    ]

    log.info("Testing search...")

    for query in queries:
        log.info("Query: %s", query)

        context = mc.search(
            query,
            user_id=USER_ID,
        )

        if context is None:
            raise RuntimeError(
                f"Search returned no result for: {query}"
            )

        pretty_print_context(
            context,
            query=query,
        )

    log.info("Testing forget()...")

    memories = mc.inspect(
        user_id=USER_ID,
        view="memories",
    )

    if not memories:
        raise RuntimeError(
            "No memories available to test forget()."
        )

    memory_id = memories[0]["id"]

    log.info("Forgetting memory: %s", memory_id)

    forgotten = mc.forget(
        memory_id,
        user_id=USER_ID,
    )

    if not forgotten:
        raise RuntimeError(
            f"Failed to forget memory {memory_id}"
        )

    remaining = mc.inspect(
        user_id=USER_ID,
        view="memories",
    )

    if any(memory["id"] == memory_id for memory in remaining):
        raise RuntimeError(
            f"Memory {memory_id} still exists after forget()."
        )

    log.info("forget() passed.")

    log.info("Testing reset()...")

    mc.reset(user_id=USER_ID)

    memories = mc.inspect(
        user_id=USER_ID,
        view="memories",
    )

    if memories:
        raise RuntimeError(
            "reset() did not remove all memories."
        )

    log.info("reset() passed.")

    try:
        from mindcache.Database.db_setup import engine
        engine.dispose()
    except Exception as e:
        log.warning("Database cleanup warning: %s", e)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    log.info("All MindCache client API tests passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("MindCache client API test failed.")
        raise