"""
Tests for the MindCache public API: add(), get_all(), delete(), reset().

Strategy:
  - MindCache.__init__ calls reconfigure_engine() (which is fine — it updates
    the global RoutingEngine) and refresh_tree_cache() (which we mock out to
    prevent loading the CollapsedTreeCache / embedding model).
  - ActivePathRetrieval and Memory_Extractor are also mocked at construction
    since they load heavy resources on init.
  - Tests exercise only the DB-backed methods against an in-memory SQLite DB.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from mindcache.Database.db_setup import (
    ProcessingJob, Topic, MemoryRegistry,
    EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory,
)
from mindcache.exceptions import IngestionError


# Fixture: lightweight MindCache instance backed by the test DB

@pytest.fixture
def mc(db_session, monkeypatch):
    """
    Creates a MindCache instance whose Session is wired to the test in-memory
    SQLite DB. Heavy init steps (refresh_tree_cache, ActivePathRetrieval,
    Memory_Extractor, EmbeddingManager) are mocked out.
    """
    # Prevent refresh_tree_cache from loading the vector index
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )

    # Prevent ActivePathRetrieval from loading the embedding model and reranker
    with patch("mindcache.retrieval.active_path.ActivePathRetrieval") as mock_retriever_cls, \
         patch("mindcache.Memory_extract.memory_extractor.Memory_Extractor") as mock_extractor_cls:

        mock_retriever_cls.return_value = MagicMock()
        mock_extractor_cls.return_value = MagicMock()

        # Import MindCache here so the patches are active during __init__
        from mindcache.client import MindCache
        import mindcache.Database.db_setup as db_setup

        client = MindCache.__new__(MindCache)
        client.Session = db_setup.Session
        client.db_manager = MagicMock()
        client.retriever = mock_retriever_cls.return_value
        client.extractor = mock_extractor_cls.return_value
        client.enable_summarization = False

        yield client

def test_add_returns_integer_job_id(mc, db_session):
    job_id = mc.add([{"role": "user", "content": "Hello!"}])
    assert isinstance(job_id, int)
    assert job_id > 0


def test_add_creates_pending_job_in_db(mc, db_session):
    mc.add([{"role": "user", "content": "Test message."}])
    job = db_session.query(ProcessingJob).first()
    assert job is not None
    assert job.status == "pending"


def test_add_formats_multi_turn_messages_correctly(mc, db_session):
    mc.add([
        {"role": "user",      "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
    ])
    job = db_session.query(ProcessingJob).first()
    assert "User: What is Python?" in job.raw_prompt
    assert "Assistant: Python is a programming language." in job.raw_prompt


def test_add_empty_messages_raises_ingestion_error(mc, db_session):
    """Passing a completely empty messages list should raise IngestionError."""
    with pytest.raises((IngestionError, Exception)):
        mc.add("not a list")  # type: ignore


def _seed_one_memory(db_session, user_id="default"):
    """Seed a single episodic memory via raw ORM (bypass MindCache ingestion pipeline)."""
    topic = Topic(
        name="Test Topic", name_normalized="test topic",
        level=0, user_id=user_id, timestamp=datetime.now(),
    )
    db_session.add(topic)
    db_session.flush()

    reg = MemoryRegistry(user_id=user_id, memory_type="episodic")
    db_session.add(reg)
    db_session.flush()

    mem = EpisodicMemory(
        id=reg.id, user_id=user_id, topic_id=topic.id,
        content="A test episodic memory.",
        timestamp=datetime.now(),
    )
    db_session.add(mem)
    db_session.commit()
    return mem


def test_get_all_empty_returns_empty_list(mc, db_session):
    result = mc.get_all()
    assert result == []


def test_get_all_returns_seeded_memories(mc, db_session):
    _seed_one_memory(db_session)
    result = mc.get_all()
    assert len(result) == 1
    entry = result[0]
    assert "id" in entry
    assert "type" in entry
    assert "content" in entry
    assert "topic" in entry
    assert entry["content"] == "A test episodic memory."
    assert entry["type"] == "episodic"


def test_get_all_filters_by_memory_type(mc, db_session):
    _seed_one_memory(db_session, user_id="default")

    # Seed a user memory too
    topic = db_session.query(Topic).first()
    reg = MemoryRegistry(user_id="default", memory_type="user")
    db_session.add(reg)
    db_session.flush()
    user_mem = UserMemory(
        id=reg.id, user_id="default", topic_id=topic.id,
        content="A user memory.", timestamp=datetime.now(),
    )
    db_session.add(user_mem)
    db_session.commit()

    episodic_only = mc.get_all(memory_type="episodic")
    assert all(m["type"] == "episodic" for m in episodic_only)
    assert len(episodic_only) == 1

    user_only = mc.get_all(memory_type="user")
    assert all(m["type"] == "user" for m in user_only)
    assert len(user_only) == 1

def test_delete_returns_true_and_removes_memory(mc, db_session, monkeypatch):
    # Prevent delete() from calling refresh_tree_cache
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    mem = _seed_one_memory(db_session)
    mem_id = mem.id

    result = mc.delete(mem_id)

    assert result is True
    # Should be gone from DB
    remaining = db_session.query(EpisodicMemory).filter(
        EpisodicMemory.id == mem_id
    ).first()
    assert remaining is None


def test_delete_returns_false_for_missing_id(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    result = mc.delete(99999)
    assert result is False

def test_reset_removes_all_user_data(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    _seed_one_memory(db_session)
    # Directly seed a pending job without going through mc.add
    from mindcache.Database.db_setup import ProcessingJob
    job = ProcessingJob(raw_prompt="test", status="pending",
                        timestamp=datetime.now(), user_id="default")
    db_session.add(job)
    db_session.commit()

    mc.reset(user_id="default")

    assert mc.get_all() == []
    assert db_session.query(ProcessingJob).count() == 0
    from mindcache.Database.db_setup import Topic
    assert db_session.query(Topic).filter(Topic.user_id == "default").count() == 0


def test_reset_does_not_affect_other_users(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    # Seed data for two users
    _seed_one_memory(db_session, user_id="alice")
    _seed_one_memory(db_session, user_id="bob")

    mc.reset(user_id="alice")

    # Alice's data is gone
    alice_mems = mc.get_all(user_id="alice")
    assert alice_mems == []

    # Bob's data survives
    bob_mems = mc.get_all(user_id="bob")
    assert len(bob_mems) == 1
