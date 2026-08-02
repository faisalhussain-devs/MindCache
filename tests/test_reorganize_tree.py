"""
Full behavioral tests for the tree reorganization pipeline (reorganize_tree.py).

Key insight: apply_mapping() accepts a `raw_json` parameter that bypasses the
LLM call entirely. This lets us test all reorganization logic (merges, renames,
reparenting, dedup, orphan cleanup) without any LLM or network dependency.
"""

import json
import pytest
from datetime import datetime
from mindcache.Database.db_setup import (
    Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory,
    MemoryRegistry,
)
from mindcache.Database.reorganize_tree import apply_mapping, normalize_topic_name


def test_normalize_lowercases_and_singularizes(db_session):
    assert normalize_topic_name("Algorithms") == "algorithm"
    assert normalize_topic_name("Strategies") == "strategy"
    assert normalize_topic_name("Python") == "python"


def test_normalize_strips_general_prefix(db_session):
    assert normalize_topic_name("General Sleep") == "sleep"


def test_normalize_preserves_double_s(db_session):
    assert normalize_topic_name("stress") == "stress"


def _seed_topic(session, name, level=0, parent_id=None, user_id="default"):
    t = Topic(
        name=name,
        name_normalized=normalize_topic_name(name),
        level=level,
        parent_id=parent_id,
        user_id=user_id,
        timestamp=datetime.now(),
    )
    session.add(t)
    session.flush()
    return t


def _seed_memory(session, topic_id, content="Test memory.", user_id="default"):
    from conftest import _make_vec, _vec_to_blob
    import random
    reg = MemoryRegistry(user_id=user_id, memory_type="episodic")
    session.add(reg)
    session.flush()
    mem = EpisodicMemory(
        id=reg.id, user_id=user_id, topic_id=topic_id,
        content=content,
        embedding=_vec_to_blob(_make_vec(random.randint(1, 9999))),
        timestamp=datetime.now(),
    )
    session.add(mem)
    session.flush()
    return mem


def _run_mapping(session, changes: list, user_id="default"):
    """Wrap apply_mapping with a pre-built raw_json, avoiding any LLM call."""
    # apply_mapping requires target_node_ids — pass all existing topic IDs
    from mindcache.Database.db_setup import Topic
    all_ids = [t.id for t in session.query(Topic).filter(Topic.user_id == user_id).all()]
    raw_json = json.dumps({"modified_nodes_only": changes})
    result = apply_mapping(session, user_id=user_id, raw_json=raw_json, target_node_ids=all_ids)
    session.commit()
    return result


def test_rename_node(db_session):
    topic = _seed_topic(db_session, "Pyhton")  # deliberate typo
    _seed_memory(db_session, topic.id, content="Python memory.")
    db_session.commit()

    _run_mapping(db_session, [{"id": topic.id, "name": "Python"}])

    db_session.refresh(topic)
    assert topic.name == "Python"


def test_merge_duplicate_nodes_transfers_memories(db_session):
    primary = _seed_topic(db_session, "Python")
    duplicate = _seed_topic(db_session, "Py")
    mem = _seed_memory(db_session, duplicate.id, content="Duplicate memory content.")
    db_session.commit()

    _run_mapping(db_session, [{"id": duplicate.id, "merged_into_id": primary.id}])

    # Duplicate topic should be gone
    remaining = db_session.query(Topic).filter(Topic.id == duplicate.id).first()
    assert remaining is None

    # Memory should now belong to the primary topic
    moved_mem = db_session.query(EpisodicMemory).filter(
        EpisodicMemory.id == mem.id
    ).first()
    assert moved_mem is not None
    assert moved_mem.topic_id == primary.id


def test_reparent_node_to_existing_parent(db_session):
    health = _seed_topic(db_session, "Health", level=0)
    fitness = _seed_topic(db_session, "Fitness", level=0)
    sleep = _seed_topic(db_session, "Sleep", level=1, parent_id=health.id)
    _seed_memory(db_session, sleep.id, content="Sleep memory.")
    db_session.commit()

    _run_mapping(db_session, [{"id": sleep.id, "parent_id": fitness.id}])

    db_session.refresh(sleep)
    assert sleep.parent_id == fitness.id

def test_create_new_parent_category(db_session):
    meditation = _seed_topic(db_session, "Meditation", level=0)
    yoga = _seed_topic(db_session, "Yoga", level=0)
    _seed_memory(db_session, meditation.id, content="Meditation memory.")
    _seed_memory(db_session, yoga.id, content="Yoga memory.")
    db_session.commit()

    _run_mapping(db_session, [
        {"id": meditation.id, "parent_id": "NEW_Mindfulness"},
        {"id": yoga.id,       "parent_id": "NEW_Mindfulness"},
    ])

    # A "Mindfulness" topic should now exist
    mindfulness = db_session.query(Topic).filter(
        Topic.name.ilike("%mindfulness%"),
        Topic.user_id == "default"
    ).first()
    assert mindfulness is not None

    db_session.refresh(meditation)
    db_session.refresh(yoga)
    assert meditation.parent_id == mindfulness.id
    assert yoga.parent_id == mindfulness.id

def test_sibling_dedup_same_normalized_name(db_session):
    """
    Two siblings with names that normalize identically should be deduplicated.
    We simulate this by merging via apply_mapping (the production reorganize_tree
    flow would detect this via normalize comparison — we test the merge directly).
    """
    parent = _seed_topic(db_session, "Computer Science", level=0)
    py1 = _seed_topic(db_session, "Python",  level=1, parent_id=parent.id)
    py2 = _seed_topic(db_session, "Pythons", level=1, parent_id=parent.id)  # normalizes to "python"
    mem1 = _seed_memory(db_session, py1.id, content="Memory on py1.")
    mem2 = _seed_memory(db_session, py2.id, content="Memory on py2.")
    db_session.commit()

    # Merge py2 into py1
    _run_mapping(db_session, [{"id": py2.id, "merged_into_id": py1.id}])

    # py2 should be gone
    assert db_session.query(Topic).filter(Topic.id == py2.id).first() is None
    # Both memories should now be on py1
    mems = db_session.query(EpisodicMemory).filter(
        EpisodicMemory.topic_id == py1.id
    ).all()
    assert len(mems) == 2

def test_empty_changeset_is_noop(db_session):
    topic = _seed_topic(db_session, "Mathematics", level=0)
    _seed_memory(db_session, topic.id, content="Some math fact.")
    db_session.commit()

    # Empty list of changes — nothing should change
    _run_mapping(db_session, [])

    db_session.refresh(topic)
    assert topic.name == "Mathematics"
    count = db_session.query(Topic).filter(Topic.user_id == "default").count()
    assert count == 1

def test_level_recalculation_after_reparent(db_session):
    """
    Moving a level-2 node to be a direct child of root should update its level.
    apply_mapping does NOT automatically recalculate levels — that is handled
    by the caller (reorganize_tree). This test verifies parent_id was set correctly;
    level correction happens in the broader reorganize pipeline.
    """
    root = _seed_topic(db_session, "Root", level=0)
    mid = _seed_topic(db_session, "Mid", level=1, parent_id=root.id)
    deep = _seed_topic(db_session, "Deep", level=2, parent_id=mid.id)
    _seed_memory(db_session, deep.id, content="Deep memory.")
    db_session.commit()

    # Move 'deep' directly under root
    _run_mapping(db_session, [{"id": deep.id, "parent_id": root.id}])

    db_session.refresh(deep)
    # parent_id must now point to root
    assert deep.parent_id == root.id
