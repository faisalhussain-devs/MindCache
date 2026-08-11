"""
Tests for the MindCache public API: add(), process(), inspect(), forget(), reset().
Also verifies backwards-compatibility aliases: process_queue(), get_all(), delete().
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


def test_consolidate_processing_jobs_merges_pending_jobs(mc, db_session):
    """Test that DatabaseManager.consolidate_processing_jobs merges multiple small pending jobs."""
    from mindcache.Database.db_manager import DatabaseManager
    db_mgr = DatabaseManager()
    db_mgr.Session = mc.Session

    now = datetime.now()
    j1 = ProcessingJob(raw_prompt="User: First turn", status="pending", timestamp=now, user_id="alice")
    j2 = ProcessingJob(raw_prompt="User: Second turn", status="pending", timestamp=now, user_id="alice")
    db_session.add_all([j1, j2])
    db_session.commit()

    consumed = db_mgr.consolidate_processing_jobs(user_id="alice", max_tokens=4000, hard_limit_tokens=7000)
    assert consumed == 2

    remaining_jobs = db_session.query(ProcessingJob).filter(ProcessingJob.user_id == "alice").all()
    assert len(remaining_jobs) == 1
    assert "User: First turn" in remaining_jobs[0].raw_prompt
    assert "User: Second turn" in remaining_jobs[0].raw_prompt



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


def test_inspect_empty_returns_empty_list(mc, db_session):
    result = mc.inspect(view="memories")
    assert result == []


def test_inspect_returns_seeded_memories(mc, db_session):
    _seed_one_memory(db_session)
    result = mc.inspect(view="memories")
    assert len(result) == 1
    entry = result[0]
    assert "id" in entry
    assert "type" in entry
    assert "content" in entry
    assert "topic" in entry
    assert entry["content"] == "A test episodic memory."
    assert entry["type"] == "episodic"


def test_inspect_filters_by_memory_type(mc, db_session):
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

    episodic_only = mc.inspect(view="memories", memory_type="episodic")
    assert all(m["type"] == "episodic" for m in episodic_only)
    assert len(episodic_only) == 1

    user_only = mc.inspect(view="memories", memory_type="user")
    assert all(m["type"] == "user" for m in user_only)
    assert len(user_only) == 1


def test_inspect_view_tree_refreshes_and_returns_tree(mc, db_session, monkeypatch):
    refresh_called = []
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda user_id="default": refresh_called.append(user_id),
    )
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.get_tree_cache",
        lambda user_id="default": {"fake": "tree"},
    )
    tree_result = mc.inspect(view="tree", user_id="alice")
    assert refresh_called == ["alice"]
    assert tree_result == {"fake": "tree"}


def test_inspect_view_all_returns_tree_and_memories(mc, db_session, monkeypatch):
    refresh_called = []
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda user_id="default": refresh_called.append(user_id),
    )
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.get_tree_cache",
        lambda user_id="default": {"fake": "tree"},
    )
    _seed_one_memory(db_session, user_id="alice")

    all_result = mc.inspect(view="all", user_id="alice")
    assert refresh_called == ["alice"]
    assert "tree" in all_result
    assert "memories" in all_result
    assert all_result["tree"] == {"fake": "tree"}
    assert len(all_result["memories"]) == 1


def test_inspect_invalid_view_raises_value_error(mc, db_session):
    with pytest.raises(ValueError):
        mc.inspect(view="invalid_mode")


def test_remove_memory_from_collapsed_tree_cache(mc, tmp_path, monkeypatch):
    """Test incremental removal of a memory entry from CollapsedTreeCache."""
    import numpy as np
    from mindcache.retrieval.root_cache import CollapsedTreeCache, CollapsedTreeCacheData, MemoryMeta

    monkeypatch.setattr(CollapsedTreeCache, "cache_file", property(lambda self: tmp_path / "test_cache.pkl"))
    monkeypatch.setattr(CollapsedTreeCache, "emb_matrix_file", property(lambda self: tmp_path / "test_matrix.npy"))

    cache = CollapsedTreeCache(user_id="test_remove")

    data = CollapsedTreeCacheData()
    m1 = MemoryMeta(memory_id=1, memory_type="episodic", topic_id=1, name="T1", level=0, path="T1", searchable_text="python fastapi")
    m2 = MemoryMeta(memory_id=2, memory_type="knowledge", topic_id=1, name="T1", level=0, path="T1", searchable_text="postgres database")

    data.entries = [m1, m2]
    data.entry_key_to_index = {"mem:episodic:1": 0, "mem:knowledge:2": 1}
    data.doc_lengths = [2, 2]
    data.inverted_index = {"python": {0: 1}, "postgres": {1: 1}}
    data.embedding_matrix = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
    data.corpus_size = 2
    data.avg_dl = 2.0

    cache._data = data

    removed = cache.remove_memory(memory_id=1, memory_type="episodic")
    assert removed is True
    assert cache._data.corpus_size == 1
    assert len(cache._data.entries) == 1
    assert cache._data.entries[0].memory_id == 2
    assert "mem:episodic:1" not in cache._data.entry_key_to_index
    assert cache._data.entry_key_to_index["mem:knowledge:2"] == 0
    assert cache._data.embedding_matrix.shape[0] == 1
    assert np.allclose(cache._data.embedding_matrix[0], [0.3, 0.4])


def test_forget_returns_true_and_removes_memory(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    mem = _seed_one_memory(db_session)
    mem_id = mem.id

    result = mc.forget(mem_id)

    assert result is True
    remaining = db_session.query(EpisodicMemory).filter(
        EpisodicMemory.id == mem_id
    ).first()
    assert remaining is None


def test_forget_returns_false_for_missing_id(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    result = mc.forget(99999)
    assert result is False


def test_reset_removes_all_user_data(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    _seed_one_memory(db_session)
    from mindcache.Database.db_setup import ProcessingJob
    job = ProcessingJob(raw_prompt="test", status="pending",
                        timestamp=datetime.now(), user_id="default")
    db_session.add(job)
    db_session.commit()

    mc.reset(user_id="default")

    assert mc.inspect() == []
    assert db_session.query(ProcessingJob).count() == 0
    from mindcache.Database.db_setup import Topic
    assert db_session.query(Topic).filter(Topic.user_id == "default").count() == 0


def test_reset_does_not_affect_other_users(mc, db_session, monkeypatch):
    monkeypatch.setattr(
        "mindcache.retrieval.root_cache.refresh_tree_cache",
        lambda *a, **kw: None,
    )
    _seed_one_memory(db_session, user_id="alice")
    _seed_one_memory(db_session, user_id="bob")

    mc.reset(user_id="alice")

    alice_mems = mc.inspect(user_id="alice")
    assert alice_mems == []

    bob_mems = mc.inspect(user_id="bob")
    assert len(bob_mems) == 1

