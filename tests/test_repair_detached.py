"""
Tests for repair_detached_memories_for_user().

Uses real numpy vector arithmetic (cosine similarity) against real in-memory
SQLite data. No LLM or embedding model required — we seed synthetic vectors
that are designed to point in controlled directions so we can assert exactly
which topic/sibling should win the similarity race.
"""

import numpy as np
import pytest
from datetime import datetime
from mindcache.Database.db_setup import (
    Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory,
    MemoryRegistry, to_numpy,
)
from mindcache.Database.repair_detached_memories import repair_detached_memories_for_user

def _blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


# Canonical direction vectors — clearly separated in 768-d space
DIM = 768
VEC_A = _unit(np.zeros(DIM, dtype=np.float32) + np.eye(DIM, dtype=np.float32)[0])   # [1,0,0,...]
VEC_B = _unit(np.zeros(DIM, dtype=np.float32) + np.eye(DIM, dtype=np.float32)[1])   # [0,1,0,...]
VEC_C = _unit(np.zeros(DIM, dtype=np.float32) + np.eye(DIM, dtype=np.float32)[2])   # [0,0,1,...]
# "Close to A" — slightly perturbed
VEC_A_CLOSE = _unit(VEC_A + 0.01 * VEC_B)


def _add_topic(session, name, embedding=None, user_id="default", level=0, parent_id=None):
    t = Topic(
        name=name,
        name_normalized=name.lower(),
        level=level,
        parent_id=parent_id,
        user_id=user_id,
        embedding=_blob(embedding) if embedding is not None else None,
        timestamp=datetime.now(),
    )
    session.add(t)
    session.flush()
    return t


def _add_mem(session, topic_id, embedding, content="Content.", message_id=None,
             mtype="episodic", user_id="default"):
    if message_id is not None:
        from mindcache.Database.db_setup import TriadBlock
        tb = session.get(TriadBlock, message_id)
        if not tb:
            tb = TriadBlock(id=message_id, user_id=user_id, raw_msg="test message")
            session.add(tb)
            session.flush()
    # Always create the MemoryRegistry row first — memory IDs are FKs to it
    reg = MemoryRegistry(user_id=user_id, memory_type=mtype)
    session.add(reg)
    session.flush()
    MemClass = {"episodic": EpisodicMemory, "knowledge": KnowledgeMemory,
                "user": UserMemory, "decision": DecisionMemory}[mtype]
    mem = MemClass(
        id=reg.id,
        user_id=user_id,
        topic_id=topic_id,   # May be None for detached memories
        content=content,
        embedding=_blob(embedding) if embedding is not None else None,
        message_id=message_id,
        timestamp=datetime.now(),
    )
    session.add(mem)
    session.flush()
    return mem


def test_repair_uses_sibling_vector_similarity(db_session):
    """
    Detached memory (no topic_id) shares a message_id with a valid sibling.
    The sibling's vector is close to the detached mem's vector → detached mem
    should be assigned the sibling's topic_id.
    """
    topic_a = _add_topic(db_session, "Python", embedding=VEC_A)
    topic_b = _add_topic(db_session, "Health", embedding=VEC_B)
    db_session.commit()

    MSG_ID = 42
    # Valid sibling: belongs to topic_a, embedding direction A
    sibling = _add_mem(db_session, topic_a.id, VEC_A,
                       content="Valid Python mem.", message_id=MSG_ID)
    # Detached: no topic, embedding very close to A direction
    detached = _add_mem(db_session, None, VEC_A_CLOSE,
                        content="Detached mem near Python.", message_id=MSG_ID)
    db_session.commit()

    repair_detached_memories_for_user(db_session, user_id="default")

    db_session.refresh(detached)
    assert detached.topic_id == topic_a.id

def test_repair_falls_back_to_topic_vector_search(db_session):
    """
    Detached memory with NO valid siblings in the same message group.
    Should fall back to nearest Topic embedding.
    """
    topic_a = _add_topic(db_session, "Python", embedding=VEC_A)
    topic_b = _add_topic(db_session, "Health", embedding=VEC_B)
    db_session.commit()

    MSG_ID = 99  # no other valid memories share this message_id
    # Detached memory whose vector is close to topic_b (Health)
    detached = _add_mem(db_session, None, VEC_B,
                        content="Detached mem near Health.", message_id=MSG_ID)
    db_session.commit()

    repair_detached_memories_for_user(db_session, user_id="default")

    db_session.refresh(detached)
    # Should be assigned to Health (topic_b)
    assert detached.topic_id == topic_b.id

def test_repair_skips_memories_without_embeddings(db_session):
    """
    A detached memory with embedding=None must not be processed or cause errors.
    """
    topic_a = _add_topic(db_session, "Python", embedding=VEC_A)
    db_session.commit()

    no_embed_mem = _add_mem(db_session, None, None, content="No embedding memory.", message_id=77)
    db_session.commit()

    # Must not raise
    repair_detached_memories_for_user(db_session, user_id="default")

    db_session.refresh(no_embed_mem)
    # Without an embedding, cannot be repaired — should remain None
    assert no_embed_mem.topic_id is None

def test_repair_persists_changes_to_db(db_session):
    """Repaired topic_id must be visible in a fresh query, not just in-memory."""
    topic_a = _add_topic(db_session, "Python", embedding=VEC_A)
    db_session.commit()

    MSG_ID = 55
    detached = _add_mem(db_session, None, VEC_A_CLOSE,
                        content="Detached but repairable.", message_id=MSG_ID)
    db_session.commit()
    detached_id = detached.id

    repair_detached_memories_for_user(db_session, user_id="default")

    # Fresh query — not using the cached ORM object
    fresh = db_session.query(EpisodicMemory).filter(
        EpisodicMemory.id == detached_id
    ).one()
    assert fresh.topic_id is not None
    assert fresh.topic_id == topic_a.id


def test_repair_noop_when_no_detached_memories(db_session):
    """When all memories already have topic_id, function runs cleanly with no mutations."""
    topic_a = _add_topic(db_session, "Python", embedding=VEC_A)
    # Valid memory — already anchored
    _add_mem(db_session, topic_a.id, VEC_A, content="Already anchored.")
    db_session.commit()

    count_before = db_session.query(EpisodicMemory).count()

    repair_detached_memories_for_user(db_session, user_id="default")

    count_after = db_session.query(EpisodicMemory).count()
    assert count_before == count_after
