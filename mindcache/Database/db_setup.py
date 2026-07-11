from sqlalchemy import create_engine, Column, Integer, String, Enum, ForeignKey, DateTime, LargeBinary, event
from sqlalchemy.orm import declarative_base, relationship, backref, declared_attr
from datetime import datetime

import os
db_url = os.environ.get("MINDCACHE_DB_URL")
is_postgres = db_url and db_url.startswith("postgresql")

class RoutingEngine:
    def __init__(self, fallback_engine):
        self._engine = fallback_engine

    def set_engine(self, new_engine):
        self._engine = new_engine

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def __repr__(self):
        return repr(self._engine)

from sqlalchemy.pool import NullPool

if is_postgres:
    _raw_engine = create_engine(db_url)
    try:
        from pgvector.sqlalchemy import Vector
        # nomic-embed-text-v1.5 has 768 dimensions
        VectorType = Vector(768)
    except ImportError:
        VectorType = LargeBinary
else:
    db_path = os.environ.get("MINDCACHE_DB_PATH", "mindcache.db")
    _raw_engine = create_engine(f'sqlite:///{db_path}', connect_args={'check_same_thread': False}, poolclass=NullPool)
    VectorType = LargeBinary

engine = RoutingEngine(_raw_engine)

from sqlalchemy.orm import sessionmaker
Session = sessionmaker(bind=_raw_engine)

def reconfigure_engine(db_path_or_url):
    if db_path_or_url.startswith("postgresql"):
        new_raw = create_engine(db_path_or_url)
    else:
        new_raw = create_engine(f'sqlite:///{db_path_or_url}', connect_args={'check_same_thread': False}, poolclass=NullPool)
    engine.set_engine(new_raw)
    Session.configure(bind=new_raw)

Base = declarative_base()

def to_numpy(val):
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        import numpy as np
        return np.frombuffer(val, dtype=np.float32)
    import numpy as np
    return np.asarray(val, dtype=np.float32)

from sqlalchemy.engine import Engine
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if 'sqlite' in type(dbapi_connection).__module__.lower():
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# TABLE 0: THE PROCESSING QUEUE (Buffer)
class ProcessingJob(Base):
    __tablename__ = 'processing_queue'

    id = Column(Integer, primary_key=True)
    user_id = Column(String, default="default", index=True)
    timestamp = Column(DateTime, default=datetime.now)
    raw_prompt = Column(String)
    turn_ids = Column(String)
    status = Column(String, default='pending') # pending, processing, failed
    retry_count = Column(Integer, default=0)
    embedding = Column(VectorType, nullable=True)  # BGE embedding of raw_prompt (timestamps stripped)

# TABLE 1: THE GRAPH (Structure & Intent)
class Topic(Base):
    __tablename__ = 'topics'

    id = Column(Integer, primary_key=True)
    user_id = Column(String, default="default", index=True)
    name = Column(String, nullable=False, index=True)   # e.g., "Skeletonizer"
    name_normalized = Column(String, index=True)        # case-insensitive search name e.g., "skeletonizer"
    level = Column(Integer, default=0)      # 0=Root, 1=Branch...
    summary = Column(String)                # The "RAPTOR" Summary 
    embedding = Column(VectorType)
    description = Column(String) # description of the node and its subnodes, helpful for retrieval
    timestamp = Column(DateTime, default=datetime.now) 
    last_consolidated_at = Column(DateTime, nullable=True)

    parent_id = Column(Integer, ForeignKey('topics.id'), nullable=True)
    children = relationship("Topic", 
                          backref=backref('parent', remote_side=[id]),
                          order_by="Topic.id"
                          )
    
    episodic_memories = relationship("EpisodicMemory", back_populates="topic")
    user_memories = relationship("UserMemory", back_populates="topic")
    knowledge_memories = relationship("KnowledgeMemory", back_populates="topic")
    decision_memories = relationship("DecisionMemory", back_populates="topic")

# TABLE 2: THE HISTORY (Time & Source)

class TriadBlock(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)             
    user_id = Column(String, default="default", index=True)
    timestamp = Column(DateTime, default=datetime.now)
    raw_msg = Column(String)
    source_session_id = Column(String, nullable=True, index=True)  # Provenance: which session this came from
    generated_episodic = relationship("EpisodicMemory", back_populates="message")
    generated_user = relationship("UserMemory", back_populates="message")
    generated_knowledge = relationship("KnowledgeMemory", back_populates="message")
    generated_decision = relationship("DecisionMemory", back_populates="message")

# TABLE 3: GLOBAL MEMORY REGISTRY (Unified ID Space)

class MemoryRegistry(Base):
    __tablename__ = 'memory_registry'

    id = Column(Integer, primary_key=True)
    user_id = Column(String, default="default", index=True)
    memory_type = Column(
        Enum("episodic", "user", "knowledge", "decision", name="memory_type_enum"),
        nullable=False
    )

# TABLE 4: THE ATOMS (The Searchable Units)

class BaseMemory(Base):
    __abstract__ = True
    id = Column(Integer, ForeignKey('memory_registry.id'), primary_key=True)
    user_id = Column(String, default="default", index=True)
    content = Column(String)
    timestamp = Column(DateTime, default=datetime.now)
    source_message_ids = Column(String, nullable=True)
    embedding = Column(VectorType, nullable=True)  # Per-memory vector for individual retrieval

    @declared_attr
    def topic_id(cls):
        return Column(Integer, ForeignKey('topics.id'))
    
    @declared_attr
    def message_id(cls):
        return Column(Integer, ForeignKey('messages.id'))

class EpisodicMemory(BaseMemory):
    __tablename__ = 'memories_episodic'
    topic = relationship("Topic", back_populates="episodic_memories")
    message = relationship("TriadBlock", back_populates="generated_episodic")

class UserMemory(BaseMemory):
    __tablename__ = 'memories_user'
    topic = relationship("Topic", back_populates="user_memories")
    message = relationship("TriadBlock", back_populates="generated_user")

class KnowledgeMemory(BaseMemory):
    __tablename__ = 'memories_knowledge'
    topic = relationship("Topic", back_populates="knowledge_memories")
    message = relationship("TriadBlock", back_populates="generated_knowledge")

class DecisionMemory(BaseMemory):
    __tablename__ = 'memories_decision'

    status = Column(
        Enum(
            "active",
            "inactive",
            "superseded",
            "rejected",
            "conditional",
            name="decision_status"
        ),
        default="active",
        index=True
    )

    context = Column(String, nullable=True)  
    last_validated_at = Column(DateTime, default=datetime.now)

    topic = relationship("Topic", back_populates="decision_memories")
    message = relationship("TriadBlock", back_populates="generated_decision")

# Create the DB
def init_db():
    Base.metadata.create_all(engine)

if __name__ == "__main__":
    init_db()

