import numpy as np
from collections import defaultdict
import logging
logger = logging.getLogger(__name__)

def repair_detached_memories_for_user(session, user_id):
    """
    SQLAlchemy-based, database-agnostic repair function that re-anchors
    detached memories (topic_id is NULL) for a specific user.
    """
    from mindcache.Database.db_setup import Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory, to_numpy
    import numpy as np
    from collections import defaultdict

    tables_map = {
        'episodic': EpisodicMemory,
        'user': UserMemory,
        'knowledge': KnowledgeMemory,
        'decision': DecisionMemory
    }

    # 1. Load all valid memories for this user grouped by message_id
    valid_memories_by_msg = defaultdict(list)
    for tbl_label, MemClass in tables_map.items():
        query = session.query(MemClass).filter(
            MemClass.user_id == user_id,
            MemClass.topic_id.isnot(None),
            MemClass.embedding.isnot(None)
        )
        for r in query.all():
            if r.message_id is not None:
                vec = to_numpy(r.embedding).copy()
                valid_memories_by_msg[r.message_id].append({
                    'record': r,
                    'embedding': vec
                })

    # 2. Load all detached memories for this user
    detached_mems = []
    for tbl_label, MemClass in tables_map.items():
        query = session.query(MemClass).filter(
            MemClass.user_id == user_id,
            MemClass.topic_id.is_(None),
            MemClass.embedding.isnot(None)
        )
        for r in query.all():
            vec = to_numpy(r.embedding).copy()
            detached_mems.append({
                'record': r,
                'embedding': vec
            })

    if not detached_mems:
        logger.info(f"[Repair] No detached memories found for user '{user_id}'.")
        return

    logger.info(f"[Repair] Found {len(detached_mems)} detached memories for user '{user_id}'. Starting repair...")

    # 3. Load all topic embeddings for fallback search
    topics = session.query(Topic).filter(
        Topic.user_id == user_id,
        Topic.embedding.isnot(None)
    ).all()

    fallback_topics = []
    for t in topics:
        vec = to_numpy(t.embedding).copy()
        fallback_topics.append((t.id, vec))

    fallback_matrix = None
    if fallback_topics:
        fallback_matrix = np.vstack([t[1] for t in fallback_topics])
        fallback_norms = np.linalg.norm(fallback_matrix, axis=1, keepdims=True)
        fallback_norms[fallback_norms == 0] = 1.0
        fallback_matrix = fallback_matrix / fallback_norms

    # 4. Perform similarity repair
    repaired_sibling = 0
    repaired_fallback = 0

    # Group detached memories by message_id
    detached_by_msg = defaultdict(list)
    for item in detached_mems:
        detached_by_msg[item['record'].message_id].append(item)

    for msg_id, mems_detached in detached_by_msg.items():
        mems_valid = valid_memories_by_msg.get(msg_id, [])
        if mems_valid:
            # Sibling similarity
            V = np.vstack([m['embedding'] for m in mems_detached])
            V_norms = np.linalg.norm(V, axis=1, keepdims=True)
            V_norms[V_norms == 0] = 1.0
            V = V / V_norms

            W = np.vstack([m['embedding'] for m in mems_valid])
            W_norms = np.linalg.norm(W, axis=1, keepdims=True)
            W_norms[W_norms == 0] = 1.0
            W = W / W_norms

            sims = np.dot(V, W.T)
            for i, item in enumerate(mems_detached):
                best_col = np.argmax(sims[i])
                best_sibling = mems_valid[best_col]['record']
                item['record'].topic_id = best_sibling.topic_id
                repaired_sibling += 1
        else:
            # Fallback similarity
            if fallback_matrix is not None:
                V = np.vstack([m['embedding'] for m in mems_detached])
                V_norms = np.linalg.norm(V, axis=1, keepdims=True)
                V_norms[V_norms == 0] = 1.0
                V = V / V_norms

                sims = np.dot(V, fallback_matrix.T)
                for i, item in enumerate(mems_detached):
                    best_col = np.argmax(sims[i])
                    best_topic_id = fallback_topics[best_col][0]
                    item['record'].topic_id = best_topic_id
                    repaired_fallback += 1
            else:
                logger.warning(f"[Repair] Warning: No valid siblings or topics available for message {msg_id}.")

    try:
        session.commit()
        logger.info(f"[Repair] Successfully repaired: {repaired_sibling} via sibling similarity, {repaired_fallback} via topic fallback.")
    except Exception as commit_err:
        session.rollback()
        logger.error(f"[Repair] Failed to save repairs: {commit_err}")