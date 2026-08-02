"""
Full behavioral tests for RecursiveSummarizer (nodes_summary.py).

Strategy:
  - Use a real in-memory SQLite DB (seeded_db fixture).
  - Inject a MockExtractor directly onto summarizer.extractor — no network calls.
  - Test the COMPLETE workflow: leaf processing → incremental updates → forced
    rebuild → parent source-map generation → full pipeline run.
"""

import json
import pytest
from datetime import datetime
from mindcache.Database.nodes_summary import RecursiveSummarizer, LEAF_DESC_REBUILD_THRESHOLD
from mindcache.Database.db_setup import (
    EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory,
    MemoryRegistry, Topic,
)


def _add_memories(session, topic_id, count, seed_offset=100):
    """Seed `count` EpisodicMemory records onto a topic to cross the 50-mem threshold."""
    from conftest import _make_vec, _vec_to_blob
    now = datetime.now()
    for i in range(count):
        reg = MemoryRegistry(user_id="default", memory_type="episodic")
        session.add(reg)
        session.flush()
        mem = EpisodicMemory(
            id=reg.id,
            user_id="default",
            topic_id=topic_id,
            content=f"Auto-generated memory #{seed_offset + i} for topic {topic_id}.",
            embedding=_vec_to_blob(_make_vec(seed_offset + i)),
            timestamp=now,
        )
        session.add(mem)
    session.commit()


def _make_summarizer(mock_extractor):
    """Create a RecursiveSummarizer with the LLM mocked out."""
    s = RecursiveSummarizer.__new__(RecursiveSummarizer)
    from mindcache.Database.db_setup import Session
    s.Session = Session
    s.extractor = mock_extractor
    return s


def _leaf_json_response(node_ids_to_summaries: dict) -> str:
    """Build a valid LLM JSON response mapping topic id → summary text."""
    return json.dumps({str(k): v for k, v in node_ids_to_summaries.items()})

def test_get_leaf_summary_returns_correct_structure(seeded_db, mock_extractor):
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]
    ep1 = seeded_db["memories"]["ep1"]
    dec1 = seeded_db["memories"]["dec1"]

    s = _make_summarizer(mock_extractor)
    result = s.get_leaf_summary(session, python.id, user_id="default")

    # Must have both top-level keys
    assert "memories" in result
    assert "decisions" in result

    # Episodic content must appear somewhere in the memories dict
    all_mem_content = " ".join(
        content
        for ts_bucket in result["memories"].values()
        for content in ts_bucket.values()
    )
    assert "list comprehensions" in all_mem_content

    # Decision content must appear in the decisions dict
    all_dec_content = " ".join(
        content
        for ts_bucket in result["decisions"].values()
        for content in ts_bucket.values()
    )
    assert "type hints" in all_dec_content
    assert "[Decision:active]" in all_dec_content

def test_process_leaf_small_db_skips_llm(seeded_db, mock_extractor):
    """With only 3 memories (≤50), process_leaf should update summary without LLM."""
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]

    s = _make_summarizer(mock_extractor)
    # 3 total memories (2 episodic + 1 decision) — well below the 50-mem threshold
    result = s.process_leaf(session, python, total_mems=3, user_id="default")

    # Must return None — no LLM batch needed
    assert result is None
    # summary JSON must have been updated with the memory data
    assert python.summary is not None
    stored = json.loads(python.summary)
    assert "memories" in stored or "decisions" in stored
    # description is intentionally None for small leaves
    assert python.description is None
    # embedding invalidated so re-embed job picks it up
    assert python.embedding is None
    # LLM was never called
    assert mock_extractor.call_count == 0


def test_process_leaf_large_db_returns_summary_request(seeded_db, mock_extractor):
    """With >50 memories, process_leaf returns full_raw/delta_raw for LLM batching."""
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]

    # Seed 55 more memories to push past the threshold
    _add_memories(session, python.id, count=55, seed_offset=200)

    s = _make_summarizer(mock_extractor)
    result = s.process_leaf(session, python, total_mems=57, user_id="default")

    # Must return a dict requesting LLM summarization
    assert result is not None
    assert "full_raw" in result
    assert "delta_raw" in result
    # Both should contain some text
    assert len(result["full_raw"]) > 0
    # LLM not called yet — that happens in process_leaves_batched
    assert mock_extractor.call_count == 0


def test_process_leaves_batched_writes_descriptions(seeded_db, mock_extractor):
    """After batched processing, each leaf node gets a description from the mocked LLM."""
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]
    algo = seeded_db["topics"]["algo"]
    sleep = seeded_db["topics"]["sleep"]

    # Push all leaves above the 50-mem threshold
    _add_memories(session, python.id, count=55, seed_offset=300)
    _add_memories(session, algo.id,   count=55, seed_offset=400)
    _add_memories(session, sleep.id,  count=55, seed_offset=500)

    # Configure mock to return a summary for each topic id
    mock_extractor.set_response(_leaf_json_response({
        python.id: "Python summary: list comprehensions and decorators.",
        algo.id:   "Algorithms summary: binary search and quicksort.",
        sleep.id:  "Sleep summary: 8-hour preference, no screens.",
    }))

    s = _make_summarizer(mock_extractor)
    leaf_nodes = [python, algo, sleep]
    s.process_leaves_batched(session, leaf_nodes, user_id="default")

    session.refresh(python)
    session.refresh(algo)
    session.refresh(sleep)

    assert "Python summary" in python.description
    assert "Algorithms summary" in algo.description
    assert "Sleep summary" in sleep.description

    # Embeddings must be invalidated after description is written
    assert python.embedding is None
    assert algo.embedding is None
    assert sleep.embedding is None

    # LLM was called at least once
    assert mock_extractor.call_count >= 1

def test_incremental_update_uses_update_prompt(seeded_db, mock_extractor):
    """
    After an initial summarization, adding new memories and re-running should
    send an 'update' prompt (containing 'NEW MEMORIES') rather than a 'create' prompt.
    """
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]

    # Push above threshold and do first summarization
    _add_memories(session, python.id, count=55, seed_offset=600)
    mock_extractor.set_response(_leaf_json_response({python.id: "Initial Python summary."}))
    s = _make_summarizer(mock_extractor)
    s.process_leaves_batched(session, [python], user_id="default")
    session.refresh(python)

    first_call_count = mock_extractor.call_count
    assert first_call_count >= 1

    # Now add 5 more memories
    _add_memories(session, python.id, count=5, seed_offset=700)
    mock_extractor.set_response(_leaf_json_response({python.id: "Updated Python summary."}))

    s.process_leaves_batched(session, [python], user_id="default")
    session.refresh(python)

    # Must have been called again
    assert mock_extractor.call_count > first_call_count

    # The update prompt must mention "NEW MEMORIES" (not the create prompt)
    last_prompt = mock_extractor.call_args[-1]
    assert "NEW MEMORIES" in last_prompt or "EXISTING SUMMARY" in last_prompt

    # desc_update_count should have incremented
    stored = json.loads(python.summary)
    assert stored.get("desc_update_count", 0) >= 1


def test_forced_rebuild_resets_desc_update_count(seeded_db, mock_extractor):
    """After LEAF_DESC_REBUILD_THRESHOLD updates, the next run should do a full rebuild."""
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]

    # Seed enough memories to stay above 50-mem threshold
    _add_memories(session, python.id, count=55, seed_offset=800)

    s = _make_summarizer(mock_extractor)

    # Simulate the description already existing with count at threshold
    # by manually writing summary JSON with desc_update_count = threshold
    mock_extractor.set_response(_leaf_json_response({python.id: "Pre-existing summary."}))
    s.process_leaves_batched(session, [python], user_id="default")
    session.refresh(python)

    # Manually set desc_update_count to the rebuild threshold
    stored = json.loads(python.summary)
    stored["desc_update_count"] = LEAF_DESC_REBUILD_THRESHOLD
    python.summary = json.dumps(stored)
    session.commit()

    # Add new memories and re-run — should trigger full rebuild (create path)
    _add_memories(session, python.id, count=3, seed_offset=900)
    mock_extractor.set_response(_leaf_json_response({python.id: "Rebuilt Python summary."}))
    s.process_leaves_batched(session, [python], user_id="default")
    session.refresh(python)

    stored_after = json.loads(python.summary)
    # After a rebuild, desc_update_count resets to 0
    assert stored_after.get("desc_update_count", 0) == 0
    assert "Rebuilt" in python.description

def test_process_parent_single_child_inherits_description(seeded_db, mock_extractor):
    """A parent with exactly 1 child copies the child's description without any LLM call."""
    session = seeded_db["session"]
    health = seeded_db["topics"]["health"]
    sleep = seeded_db["topics"]["sleep"]

    # Give sleep a plain-text description (no [ prefix which would prevent inheritance)
    sleep.description = "User prefers 8 hours of uninterrupted sleep."
    # Also set a summary so the summary check doesn't interfere
    sleep.summary = "{}"
    sleep.timestamp = datetime.now()
    session.commit()

    s = _make_summarizer(mock_extractor)
    s.process_parent(session, health, user_id="default")
    session.commit()
    session.refresh(health)
    session.refresh(sleep)

    # Health has exactly 1 child (Sleep). The single-child path copies the description.
    assert health.description is not None
    assert "sleep" in health.description.lower() or "8 hours" in health.description
    assert mock_extractor.call_count == 0


def test_process_parent_multi_child_writes_source_map(seeded_db, mock_extractor):
    """A parent with multiple children calls the LLM and writes a source_map JSON."""
    session = seeded_db["session"]
    cs = seeded_db["topics"]["cs"]
    python = seeded_db["topics"]["python"]
    algo = seeded_db["topics"]["algo"]

    # Give each child a description so the parent has content to summarize
    python.description = "Python: list comprehensions, decorators, type hints."
    algo.description = "Algorithms: binary search O(log n), quicksort O(n log n)."
    session.commit()

    # LLM returns a cold-start source_map response
    mock_extractor.set_response(json.dumps({
        "source_map": {
            str(python.id): "CS-Python: functional style, type annotations.",
            str(algo.id):   "CS-Algorithms: classic search and sort.",
        },
        "ignored_ids": []
    }))

    s = _make_summarizer(mock_extractor)
    s.process_parent(session, cs, user_id="default")
    session.commit()
    session.refresh(cs)

    # Description should be the joined source_map values
    assert cs.description is not None
    assert len(cs.description) > 0
    # The summary JSON should contain source_map
    stored = json.loads(cs.summary)
    assert "source_map" in stored
    assert len(stored["source_map"]) == 2
    # Embedding must be invalidated
    assert cs.embedding is None
    # LLM was called once
    assert mock_extractor.call_count == 1


def test_run_full_bottom_up_pipeline(seeded_db, mock_extractor):
    """
    Full run() on the seeded DB (all leaves ≤50 memories):
    - Leaves are processed directly (no LLM for small leaves).
    - Parents should still be processed (may or may not call LLM depending on
      whether children have descriptions).
    The test just verifies the run completes without exceptions and every leaf
    node has its summary JSON updated.
    """
    session = seeded_db["session"]
    python = seeded_db["topics"]["python"]
    algo = seeded_db["topics"]["algo"]
    sleep = seeded_db["topics"]["sleep"]

    # Allow LLM to return empty / no-op for parent nodes
    mock_extractor.set_response(json.dumps({"source_map": {}, "ignored_ids": []}))

    s = _make_summarizer(mock_extractor)
    # run() creates its own internal session from self.Session
    # For this test, patch self.Session to use the test session's bind
    from sqlalchemy.orm import sessionmaker
    s.Session = sessionmaker(bind=session.bind)

    # Should not raise
    s.run(user_id="default")

    # Reload nodes from DB to check state
    fresh_session = s.Session()
    py_fresh = fresh_session.get(type(python), python.id)
    al_fresh = fresh_session.get(type(algo), algo.id)
    sl_fresh = fresh_session.get(type(sleep), sleep.id)

    # All leaf nodes should have summary JSON set (even if description is None for small leaves)
    assert py_fresh.summary is not None
    assert al_fresh.summary is not None
    assert sl_fresh.summary is not None
    fresh_session.close()
