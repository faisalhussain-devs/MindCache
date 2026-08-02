"""
Shared fixtures for the MindCache test suite.

Design principles:
  - Every test gets a fresh in-memory SQLite DB (StaticPool so all connections
    share the same database — critical for :memory: databases).
  - The global SQLAlchemy Session and engine in db_setup are patched so that
    all production code (RecursiveSummarizer, DatabaseManager, etc.) uses the
    test database without any code changes.
  - LLM calls (Summary_Extractor.summary_extract) and the ONNX embedding model
    (EmbeddingManager.encode) are mocked so tests run with zero network/GPU I/O.
"""

import json
import numpy as np
import pytest
from datetime import datetime
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _make_vec(seed: int = 0, dim: int = 768) -> np.ndarray:
    """Return a deterministic unit-length float32 vector."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _vec_to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


@pytest.fixture
def db_session(monkeypatch):
    """
    Fresh in-memory SQLite DB for each test.

    Patches:
      - mindcache.Database.db_setup.Session       → TestSession
      - mindcache.Database.db_setup.engine        → test_engine (via RoutingEngine)
      - mindcache.Database.nodes_summary.Session  → TestSession
      - mindcache.Database.reorganize_tree.Session → TestSession
      - mindcache.client.Session                  → TestSession
    """
    import mindcache.Database.db_setup as db_setup

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    # Enable FK enforcement for SQLite
    @event.listens_for(test_engine, "connect")
    def _set_pragma(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    # Create all schema tables
    db_setup.Base.metadata.create_all(test_engine)

    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    # Patch the module-level RoutingEngine proxy and Session
    monkeypatch.setattr(db_setup, "Session", TestSession)
    db_setup.engine.set_engine(test_engine)   # RoutingEngine in-place update

    # Patch modules that captured Session at import time
    try:
        import mindcache.Database.nodes_summary as ns
        monkeypatch.setattr(ns, "Session", TestSession)
    except ImportError:
        pass

    try:
        import mindcache.Database.reorganize_tree as rt
        monkeypatch.setattr(rt, "Session", TestSession)
    except ImportError:
        pass

    try:
        import mindcache.client as client_mod
        monkeypatch.setattr(client_mod, "Session", TestSession)
    except ImportError:
        pass

    session = TestSession()
    yield session
    session.close()
    db_setup.Base.metadata.drop_all(test_engine)


@pytest.fixture
def seeded_db(db_session):
    """
    Pre-populated in-memory DB:

    Tree layout:
        Computer Science  (level 0)  ← parent
          Python           (level 1)  ← leaf: 2 EpisodicMemory + 1 DecisionMemory
          Algorithms       (level 1)  ← leaf: 2 KnowledgeMemory
        Health            (level 0)  ← parent
          Sleep            (level 1)  ← leaf: 1 UserMemory

    All memories get deterministic float32 embeddings stored as bytes.
    """
    from mindcache.Database.db_setup import (
        Topic, EpisodicMemory, KnowledgeMemory, UserMemory,
        DecisionMemory, MemoryRegistry,
    )

    now = datetime.now()

    # --- topics ---
    cs = Topic(name="Computer Science", name_normalized="computer science",
               level=0, user_id="default", timestamp=now)
    health = Topic(name="Health", name_normalized="health",
                   level=0, user_id="default", timestamp=now)
    db_session.add_all([cs, health])
    db_session.flush()

    python = Topic(name="Python", name_normalized="python",
                   level=1, parent_id=cs.id, user_id="default", timestamp=now)
    algo = Topic(name="Algorithms", name_normalized="algorithm",
                 level=1, parent_id=cs.id, user_id="default", timestamp=now)
    sleep = Topic(name="Sleep", name_normalized="sleep",
                  level=1, parent_id=health.id, user_id="default", timestamp=now)
    db_session.add_all([python, algo, sleep])
    db_session.flush()

    # --- helper: create MemoryRegistry row and return its id ---
    def _reg(mtype):
        reg = MemoryRegistry(user_id="default", memory_type=mtype)
        db_session.add(reg)
        db_session.flush()
        return reg.id

    # --- Python: 2 episodic memories ---
    ep1_id = _reg("episodic")
    ep1 = EpisodicMemory(
        id=ep1_id, user_id="default", topic_id=python.id,
        content="User learned about list comprehensions in Python.",
        embedding=_vec_to_blob(_make_vec(1)),
        timestamp=now,
    )
    ep2_id = _reg("episodic")
    ep2 = EpisodicMemory(
        id=ep2_id, user_id="default", topic_id=python.id,
        content="User explored Python decorators and closures.",
        embedding=_vec_to_blob(_make_vec(2)),
        timestamp=now,
    )
    db_session.add_all([ep1, ep2])

    # --- Python: 1 decision memory ---
    dec1_id = _reg("decision")
    dec1 = DecisionMemory(
        id=dec1_id, user_id="default", topic_id=python.id,
        content="Always use type hints in Python projects for clarity.",
        status="active",
        embedding=_vec_to_blob(_make_vec(3)),
        timestamp=now,
    )
    db_session.add(dec1)

    # --- Algorithms: 2 knowledge memories ---
    kn1_id = _reg("knowledge")
    kn1 = KnowledgeMemory(
        id=kn1_id, user_id="default", topic_id=algo.id,
        content="Binary search runs in O(log n) time complexity.",
        embedding=_vec_to_blob(_make_vec(4)),
        timestamp=now,
    )
    kn2_id = _reg("knowledge")
    kn2 = KnowledgeMemory(
        id=kn2_id, user_id="default", topic_id=algo.id,
        content="QuickSort average case is O(n log n) but worst case O(n^2).",
        embedding=_vec_to_blob(_make_vec(5)),
        timestamp=now,
    )
    db_session.add_all([kn1, kn2])

    # --- Sleep: 1 user memory ---
    usr1_id = _reg("user")
    usr1 = UserMemory(
        id=usr1_id, user_id="default", topic_id=sleep.id,
        content="User prefers 8 hours of sleep and avoids screens before bed.",
        embedding=_vec_to_blob(_make_vec(6)),
        timestamp=now,
    )
    db_session.add(usr1)

    db_session.commit()

    return {
        "session": db_session,
        "topics": {
            "cs": cs, "health": health,
            "python": python, "algo": algo, "sleep": sleep,
        },
        "memories": {
            "ep1": ep1, "ep2": ep2, "dec1": dec1,
            "kn1": kn1, "kn2": kn2, "usr1": usr1,
        },
    }

class MockExtractor:
    """Replaces Summary_Extractor. Returns configurable JSON responses."""

    def __init__(self):
        self._response = None
        self.call_count = 0
        self.call_args = []

    def set_response(self, json_str: str):
        self._response = json_str

    def summary_extract(self, prompt: str):
        self.call_count += 1
        self.call_args.append(prompt)
        return self._response


@pytest.fixture
def mock_extractor():
    """Returns a MockExtractor instance. Inject into summarizer.extractor."""
    return MockExtractor()

@pytest.fixture
def mock_embedder(monkeypatch):
    """
    Patches EmbeddingManager.encode to return a zero vector.
    Prevents any ONNX model download or GPU access.
    """
    try:
        import mindcache.Database.embedder as embedder_mod

        def _fake_encode(self, texts, is_query=False):
            if isinstance(texts, str):
                texts = [texts]
            return np.zeros((len(texts), 768), dtype=np.float32)

        monkeypatch.setattr(embedder_mod.EmbeddingManager, "encode", _fake_encode)
    except ImportError:
        pass
