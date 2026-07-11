import os
import sqlite3
import numpy as np
from collections import defaultdict
import logging
logger = logging.getLogger(__name__)

def repair_database(db_path):
    logger.info(f"\n==========================================")
    logger.info(f"Repairing database: {db_path}")
    logger.info(f"==========================================")
    if not os.path.exists(db_path):
        logger.info("Database path does not exist. Skipping.")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables = ['memories_episodic', 'memories_user', 'memories_knowledge', 'memories_decision']
    
    # 1. Load all valid memories grouped by message_id
    logger.info("Loading valid memories...")
    valid_memories_by_msg = defaultdict(list)
    for tbl in tables:
        cursor.execute(f"SELECT id, message_id, topic_id, embedding FROM {tbl} WHERE topic_id IS NOT NULL AND embedding IS NOT NULL;")
        for r_id, msg_id, t_id, emb_blob in cursor.fetchall():
            if msg_id is not None:
                vec = np.frombuffer(emb_blob, dtype=np.float32)
                valid_memories_by_msg[msg_id].append({
                    'table': tbl,
                    'id': r_id,
                    'topic_id': t_id,
                    'embedding': vec
                })
                
    # 2. Load all detached memories
    logger.info("Loading detached memories...")
    detached_mems = []
    for tbl in tables:
        cursor.execute(f"SELECT id, message_id, embedding FROM {tbl} WHERE topic_id IS NULL AND embedding IS NOT NULL;")
        for r_id, msg_id, emb_blob in cursor.fetchall():
            vec = np.frombuffer(emb_blob, dtype=np.float32)
            detached_mems.append({
                'table': tbl,
                'id': r_id,
                'message_id': msg_id,
                'embedding': vec
            })
            
    logger.info(f"Found {len(detached_mems)} detached memories with embeddings.")
    if not detached_mems:
        logger.info("No repair needed.")
        conn.close()
        return

    # 3. Load all topic embeddings for fallback matrix search
    cursor.execute('SELECT id, name, embedding FROM topics WHERE embedding IS NOT NULL;')
    fallback_topics = []
    for t_id, t_name, emb_blob in cursor.fetchall():
        vec = np.frombuffer(emb_blob, dtype=np.float32)
        fallback_topics.append((t_id, t_name, vec))
        
    fallback_matrix = None
    if fallback_topics:
        fallback_matrix = np.vstack([t[2] for t in fallback_topics])
        # Normalize rows
        fallback_norms = np.linalg.norm(fallback_matrix, axis=1, keepdims=True)
        fallback_norms[fallback_norms == 0] = 1.0
        fallback_matrix = fallback_matrix / fallback_norms

    # 4. Perform matrix similarity repair
    logger.info("Repairing memories using matrix operations...")
    updates_to_run = defaultdict(list)  # table -> list of (topic_id, id)
    
    # Group detached memories by message_id to process them batch by batch
    detached_by_msg = defaultdict(list)
    for item in detached_mems:
        detached_by_msg[item['message_id']].append(item)
        
    repaired_sibling = 0
    repaired_fallback = 0
    
    for msg_id, mems_detached in detached_by_msg.items():
        mems_valid = valid_memories_by_msg.get(msg_id, [])
        
        if mems_valid:
            # Sibling matrix multiplication
            # V shape: (num_detached, dim)
            V = np.vstack([m['embedding'] for m in mems_detached])
            V_norms = np.linalg.norm(V, axis=1, keepdims=True)
            V_norms[V_norms == 0] = 1.0
            V = V / V_norms
            
            # W shape: (num_valid, dim)
            W = np.vstack([m['embedding'] for m in mems_valid])
            W_norms = np.linalg.norm(W, axis=1, keepdims=True)
            W_norms[W_norms == 0] = 1.0
            W = W / W_norms
            
            # Cosine similarity matrix: (num_detached, num_valid)
            sims = np.dot(V, W.T)
            
            for i, item in enumerate(mems_detached):
                best_col = np.argmax(sims[i])
                best_sibling = mems_valid[best_col]
                
                tbl = item['table']
                r_id = item['id']
                topic_id = best_sibling['topic_id']
                
                updates_to_run[tbl].append((topic_id, r_id))
                repaired_sibling += 1
        else:
            # Fallback matrix similarity search against topics
            if fallback_matrix is not None:
                V = np.vstack([m['embedding'] for m in mems_detached])
                V_norms = np.linalg.norm(V, axis=1, keepdims=True)
                V_norms[V_norms == 0] = 1.0
                V = V / V_norms
                
                # sims shape: (num_detached, num_topics)
                sims = np.dot(V, fallback_matrix.T)
                
                for i, item in enumerate(mems_detached):
                    best_col = np.argmax(sims[i])
                    best_topic = fallback_topics[best_col]
                    
                    tbl = item['table']
                    r_id = item['id']
                    topic_id = best_topic[0]
                    
                    updates_to_run[tbl].append((topic_id, r_id))
                    repaired_fallback += 1
            else:
                logger.warning(f"Warning: No valid siblings or topics available for message {msg_id}.")
                
    # 5. Execute DB updates in batch transaction
    logger.info("Executing batch updates in database...")
    for tbl, updates in updates_to_run.items():
        cursor.executemany(f"UPDATE {tbl} SET topic_id = ? WHERE id = ?;", updates)
        logger.info(f"  Updated {len(updates)} rows in {tbl}")
        
    conn.commit()
    conn.close()
    logger.info(f"Completed repair for {db_path}:")
    logger.info(f"  Repaired via sibling similarity matrix: {repaired_sibling}")
    logger.info(f"  Repaired via vector fallback matrix: {repaired_fallback}")

if __name__ == '__main__':
    db_paths = [
        r"E:\MindCache\mindcache.db",
        r"E:\MindCache\Test_DBs\Relationships conv[18]\mindcache.db"
    ]
    for path in db_paths:
        repair_database(path)