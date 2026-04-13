from sqlalchemy import create_engine, Column, Integer, String, Enum, ForeignKey, DateTime, LargeBinary, event
from sqlalchemy.orm import declarative_base, relationship, backref, declared_attr
from datetime import datetime

# Use check_same_thread=False for multi-threaded apps (like APIs)
engine = create_engine('sqlite:///mindcache.db', connect_args={'check_same_thread': False})
Base = declarative_base()

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL") # Optional: Makes writes even faster (safe for local tools)
    cursor.close()

# TABLE 0: THE PROCESSING QUEUE (Buffer)
class ProcessingJob(Base):
    __tablename__ = 'processing_queue'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    raw_prompt = Column(String)
    raw_response = Column(String)
    raw_next_prompt = Column(String)
    status = Column(String, default='pending') # pending, processing, failed
    retry_count = Column(Integer, default=0)

# TABLE 1: THE GRAPH (Structure & Intent)
class Topic(Base):
    __tablename__ = 'topics'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)   # e.g., "Skeletonizer"
    level = Column(Integer, default=0)      # 0=Root, 1=Branch...
    summary = Column(String)                # The "RAPTOR" Summary 
    embedding = Column(LargeBinary)
    description = Column(String) # description of the node and its subnodes, helpful for retreival
    timestamp = Column(DateTime, default=datetime.now) 
    is_groomed = Column(Integer, default=0) # SQLite doesn't have strict boolean, 0=False, 1=True
    chain_updated_at = Column(DateTime, default=datetime.now)

    parent_id = Column(Integer, ForeignKey('topics.id'), nullable=True)
    children = relationship("Topic", 
                          backref=backref('parent', remote_side=[id]),
                          order_by="Topic.id"
                          )
    
    episodic_memories = relationship("EpisodicMemory", back_populates="topic")
    user_memories = relationship("UserMemory", back_populates="topic")
    knowledge_memories = relationship("KnowledgeMemory", back_populates="topic")
    decision_memories = relationship("DecisionMemory", back_populates="topic")

class TopicEmbeddingCache(Base):
    __tablename__ = 'topic_embedding_cache'

    id = Column(Integer, primary_key=True)
    topic_leaf_id = Column(Integer, ForeignKey('topics.id'), unique=True, index=True)
    chain_embedding = Column(LargeBinary)
    last_embedded_at = Column(DateTime, default=datetime.now)

# TABLE 2: THE HISTORY (Time & Source)

class TriadBlock(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)             
    timestamp = Column(DateTime, default=datetime.now)
    raw_msg = Column(String)
    raw_llm_response = Column(String, nullable=True)
    source_session_id = Column(String, nullable=True, index=True)  # Provenance: which session this came from
    generated_episodic = relationship("EpisodicMemory", back_populates="message")
    generated_user = relationship("UserMemory", back_populates="message")
    generated_knowledge = relationship("KnowledgeMemory", back_populates="message")
    generated_decision = relationship("DecisionMemory", back_populates="message")

# TABLE 3: GLOBAL MEMORY REGISTRY (Unified ID Space)

class MemoryRegistry(Base):
    __tablename__ = 'memory_registry'

    id = Column(Integer, primary_key=True)
    memory_type = Column(
        Enum("episodic", "user", "knowledge", "decision", name="memory_type_enum"),
        nullable=False
    )

# TABLE 4: THE ATOMS (The Searchable Units)

class BaseMemory(Base):
    __abstract__ = True
    id = Column(Integer, ForeignKey('memory_registry.id'), primary_key=True)
    content = Column(String)
    timestamp = Column(DateTime, default=datetime.now)
    
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
