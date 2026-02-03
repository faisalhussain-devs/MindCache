from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Float, LargeBinary, event
from sqlalchemy.orm import declarative_base, relationship, backref
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
    timestamp = Column(Float, default=lambda: datetime.now().timestamp())
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
    timestamp = Column(Float, default=lambda: datetime.now().timestamp()) # timestamp showing the time the summary for that node was updated        
    
    # 1. The Tree Structure (Parent <-> Children)
    parent_id = Column(Integer, ForeignKey('topics.id'), nullable=True)
    children = relationship("Topic", 
                          backref=backref('parent', remote_side=[id]),
                          order_by="Topic.id"
                          )
    
    # 2. Link to Content (One Topic <-> Many Atomic Memories)
    memories = relationship("Memory", back_populates="topic")

# TABLE 2: THE HISTORY (Time & Source)

class TriadBlock(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    # The summary of the WHOLE conversation block (User + AI + User)
    summary = Column(String)                
    # Raw text (Optional, but good for debugging "Context Rot")
    raw_text = Column(String)
    timestamp = Column(DateTime, default=datetime.now)

    # Link to Content (One Message Block -> Spawned 5 Atomic Memories)
    generated_memories = relationship("Memory", back_populates="message")

# TABLE 3: THE ATOMS (The Searchable Units)
class Memory(Base):
    __tablename__ = 'memories'

    id = Column(Integer, primary_key=True)
    content = Column(String)                # "Fixed regex bug..."
    type = Column(String)                   # "episodic", "factual", "user"
    # Vector Embedding
    embedding = Column(LargeBinary)               
    # Link 1: Conceptual Location (Where does this fit in the project?)
    topic_id = Column(Integer, ForeignKey('topics.id'))
    topic = relationship("Topic", back_populates="memories")

    # Link 2: Temporal Location (When did this happen?)
    message_id = Column(Integer, ForeignKey('messages.id'))
    message = relationship("TriadBlock", back_populates="generated_memories")

# Create the DB
def init_db():
    Base.metadata.create_all(engine)
