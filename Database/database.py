import sqlite3
import json
import numpy as np
import time
from typing import List, Dict, Any

DB_FILE = "mindcache.db"

class DatabaseManager:
    def __init__(self):
        # check_same_thread=False allows multiple parts of the app to use the DB
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.cursor = self.conn.cursor()
        self._initialize_schema()

    def _initialize_schema(self):
        """Runs once to set up tables and indexes if they don't exist."""
        
        # 1. PROCESSING QUEUE (Buffer)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                raw_prompt TEXT,
                raw_response TEXT,
                raw_next_prompt TEXT,
                status TEXT DEFAULT 'pending', -- pending, processing, failed
                retry_count INTEGER DEFAULT 0
            )
        """)
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON processing_queue(status)")

        # 2. Message TABLE (Conversation Turns / Topics)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL
            )
        """)
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_turn_time ON conversation_turns(timestamp)")

        # 3. CHILD TABLE (Atomic Memories)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_atoms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timeline_id TEXT,
                memory_type TEXT,       -- 'user', 'fact', 'epis'
                content_text TEXT,
                content_embedding BLOB, -- Binary Vector for Ranking
                FOREIGN KEY(id) REFERENCES conversation_turns(id)
            )
        """)
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_atom_link ON memory_atoms(id)")

        # 4. Graph Table (nodes are topics)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level INTEGER
                topic TEXT,  
                summary TEXT,    -- summary of all messages, chats under it(in order of time)
                parent_id INTEGER,
                child_id INTEGER,
                message_ids TEXT  -- We will store the list here as a JSON string
            )
        """)
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_atom_link ON memory_atoms(timeline_id)")

        self.conn.commit()


    def _to_blob(self, vector: Any) -> bytes:
        if isinstance(vector, np.ndarray):
            return vector.astype(np.float32).tobytes()
        
        if hasattr(vector, 'detach'): 
            return vector.detach().cpu().numpy().astype(np.float32).tobytes()
        return np.array(vector, dtype=np.float32).tobytes()


    def _from_blob(self, blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)
    
    
    def _ensure_numpy(self, vector: Any) -> np.ndarray:
        """Helper to ensure query vectors are safe NumPy arrays for math."""
        if isinstance(vector, np.ndarray):
            return vector.astype(np.float32)
        if hasattr(vector, 'detach'):
            return vector.detach().cpu().numpy().astype(np.float32)
        return np.array(vector, dtype=np.float32)

    
    def add_to_queue(self, prompt: str, response: str, next_prompt: str):
        """Called when user sends a message. Fast write."""
        timestamp = time.time()
        self.cursor.execute("""
            INSERT INTO processing_queue (timestamp, raw_prompt, raw_response, raw_next_prompt)
            VALUES (?, ?, ?, ?)
        """, (timestamp, prompt, response, next_prompt))
        self.conn.commit()
        print("[DB] Added job to queue.")


    def get_pending_job(self, max_retries: int = 3):
        """
        Finds the next 'pending' OR 'failed' job.
        IMMEDIATELY marks it as 'processing' so no one else touches it.
        """
        # 1. Select the candidate (Pending OR Failed, provided it hasn't failed too many times)
        self.cursor.execute("""
            SELECT id, raw_prompt, raw_response, raw_next_prompt 
            FROM processing_queue 
            WHERE status IN ('pending', 'failed') 
              AND retry_count < ?
            ORDER BY timestamp ASC 
            LIMIT 1
        """, (max_retries,))
        
        row = self.cursor.fetchone()
        
        if row:
            job_id = row[0]
            self.cursor.execute("""
                UPDATE processing_queue 
                SET status='processing' 
                WHERE id=?
            """, (job_id,))
            self.conn.commit()
            
            return row
            
        return None
    
    def mark_job_status(self, job_id: int, status: str):
        """
        Updates the status. 
        Auto-increments retry_count ONLY if the status is being set to 'failed'.
        """
        if status == "failed":
            self.cursor.execute("""
                UPDATE processing_queue 
                SET status = ?, retry_count = retry_count + 1 
                WHERE id = ?
            """, (status, job_id))
        else:
            self.cursor.execute("""
                UPDATE processing_queue 
                SET status = ? 
                WHERE id = ?
            """, (status, job_id))
            
        self.conn.commit()


    def save_extracted_memory(self, job_id: int, extracted_data: Dict, 
                              topic_vec: Any, content_vec_map: Dict):
        """
        Saves the processed memory and deletes the temp job in ONE transaction.
        """
        try:
            timestamp = time.time()
            timeline_id = f"turn_{int(timestamp * 1000)}"
            
            topics = extracted_data.get("memory", {}).get("topics", [])
            mem_block = extracted_data.get("memory", {})
            has_content = any(len(mem_block.get(k, [])) > 0 for k in ["user", "fact", "epis"])

            if not topics or not has_content:
                self.cursor.execute("DELETE FROM processing_queue WHERE id=?", (job_id,))
                self.conn.commit()
                print(f"[DB] Job {job_id} discarded (No topics found - considered noise).")
                return 
            
            self.cursor.execute("BEGIN TRANSACTION")
            topics_json = json.dumps(topics)
            topic_blob = self._to_blob(topic_vec)
            self.cursor.execute("""
                INSERT INTO conversation_turns (timeline_id, timestamp, topics_json, topic_embedding)
                VALUES (?, ?, ?, ?)
                """, (timeline_id, timestamp, topics_json, topic_blob)
                )

            rows = []
            for m_type in ["user", "fact", "epis"]:
                texts = mem_block.get(m_type, [])
                vectors = content_vec_map.get(m_type, [])
                
                if not texts or len(texts) != len(vectors):
                    continue

                for i, text in enumerate(texts):
                    blob = self._to_blob(vectors[i])
                    rows.append((timeline_id, m_type, text, blob))
            
            self.cursor.executemany("""
                INSERT INTO memory_atoms (timeline_id, memory_type, content_text, content_embedding)
                VALUES (?, ?, ?, ?)
            """, rows)

            self.cursor.execute("DELETE FROM processing_queue WHERE id=?", (job_id,))
            
            self.conn.commit()
            print(f"[DB] Success! Job {job_id} moved to Vault (ID: {timeline_id})")
            
        except Exception as e:
            self.conn.rollback() # Undo everything if anything failed
            print(f"[DB Error] Transaction failed: {e}")
            self.mark_job_status(job_id, "failed")

    
    def search(self, query_topic_vec: Any, query_content_vec: Any, limit=5, TOPIC_THRESHOLD=0.6):
        """
        1. Scans Parent Table (Topic) -> Filter
        2. Scans Child Table (Content) -> Rank
        """
        q_topic = self._ensure_numpy(query_topic_vec)
        q_content = self._ensure_numpy(query_content_vec)

        self.cursor.execute("SELECT timeline_id, topic_embedding FROM conversation_turns")
        parents = self.cursor.fetchall()
        
        candidates = []
        for tid, blob in parents:
            t_vec = self._from_blob(blob)
            score = np.dot(t_vec, q_topic)
            if score > TOPIC_THRESHOLD: # Topic Threshold
                candidates.append(tid)

        if not candidates:
            return []

        # Step 2: Rank Children (Fine Search)
        # We assume `candidates` is a list of strings like ['turn_123', 'turn_456']
        placeholders = ','.join('?' * len(candidates))
        query = f"""
            SELECT content_text, content_embedding, memory_type 
            FROM memory_atoms 
            WHERE timeline_id IN ({placeholders})
        """
        self.cursor.execute(query, candidates)
        
        results = []
        for text, blob, m_type in self.cursor.fetchall():
            c_vec = self._from_blob(blob)
            final_score = np.dot(c_vec, q_content)
            
            results.append({
                "text": text,
                "type": m_type,
                "score": float(final_score)
            })
            
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]