import sys
import json
import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, TopicEmbeddingCache
from Database.embedder import EmbeddingManager
from Memory_extract.safe_ai import SafeAI
from pydantic import BaseModel, Field
from typing import List, Union, Optional

class ReorganizedNode(BaseModel):
    id: Union[int, str] = Field(description="Original ID of the node.")
    name: Optional[str] = Field(default=None, description="OMIT THIS KEY if the name is not changing. Only include if renaming the category.")
    parent_id: Optional[Union[int, str]] = Field(default=None, description="OMIT THIS KEY if the parent is not changing. Only include if re-parenting. Use 'NEW_Root' for a new root.")
    merged_into_id: Optional[int] = Field(default=None, description="OMIT THIS KEY if the node is not being merged. Only include if merging into another ID.")

class TreeReorganizationSchema(BaseModel):
    modified_nodes_only: List[ReorganizedNode] = Field(description="ONLY include nodes that require a change (e.g., merging, newly assigned parent, or renamed). Do NOT include nodes that are already correctly placed and need no changes.")


TOKEN_LIMIT = 3000

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def build_branch_text(node, depth=0) -> str:
    """Recursively renders a tree branch as indented text. Uses ORM children relationship."""
    text = "  " * depth + f"[{node.id}] {node.name}\n"
    for child in node.children:
        text += build_branch_text(child, depth + 1)
    return text

def build_chain_path_text(node) -> str:
    """Builds root -> ... -> leaf path text for embedding. Uses ORM parent relationship."""
    curr = node
    path = []
    while curr:
        path.append(curr.name)
        curr = curr.parent
    path.reverse()
    path_str = " -> ".join(path)
    desc = f": {node.description}" if node.description else ""
    return f"{path_str}{desc}"

def _get_all_descendants(node):
    """Recursively collects a node and all its descendants."""
    result = [node]
    for child in node.children:
        result.extend(_get_all_descendants(child))
    return result

def _would_create_cycle(node_map, child_id, proposed_parent_id):
    """Walk ancestors of proposed_parent_id; if we hit child_id it's a cycle."""
    visited = set()
    curr_id = proposed_parent_id
    while curr_id is not None:
        if curr_id == child_id or curr_id in visited:
            return True
        visited.add(curr_id)
        parent_node = node_map.get(curr_id)
        curr_id = parent_node.parent_id if parent_node else None
    return False


def update_leaf_embedding_cache(session, nodes, embedder):
    """Updates the embedding cache for leaf nodes. Can be called standalone after any tree mutation."""
    leaf_nodes = [n for n in nodes if not n.children]
    if not leaf_nodes:
        return
    print(f"  Updating embedding cache for {len(leaf_nodes)} leaf nodes...")
    chain_texts = [build_chain_path_text(node) for node in leaf_nodes]
    all_vecs = embedder.get_batch_embeddings(chain_texts)
    for node, vec in zip(leaf_nodes, all_vecs):
        vec_bytes = np.array(vec, dtype=np.float32).tobytes()
        cache_entry = session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == node.id).first()
        if cache_entry:
            cache_entry.chain_embedding = vec_bytes
            cache_entry.last_embedded_at = datetime.now()
        else:
            session.add(TopicEmbeddingCache(
                topic_leaf_id=node.id,
                chain_embedding=vec_bytes,
                last_embedded_at=datetime.now()
            ))

def apply_mapping(session, ai, prompt, system_prompt, target_nodes, embedder, dry_run=True):
    raw_json = ai.generate(prompt=prompt, system_prompt=system_prompt, json_schema=TreeReorganizationSchema.model_json_schema())
    if not raw_json:
        print("Failed to get LLM response.")
        return
    try:
        print(len(raw_json))
        data = json.loads(raw_json)
        nodes_data = data.get("modified_nodes_only", [])
        print(len(nodes_data))
    except json.JSONDecodeError as e:
        print(f"JSON Error: {e}\n{raw_json}")
        return
        
    existing_nodes = session.query(Topic).all()
    node_map = {n.id: n for n in existing_nodes}
    
    # 1. Create NEW Parents (case-insensitive, stored as dict for lookup)
    new_parents_db = {}
    for n_data in nodes_data:
        pid = n_data.get("parent_id")
        if not isinstance(pid, str):
            continue
        pid_key = pid.lower()
        if pid_key.startswith("new_") and pid_key not in new_parents_db:
            clean_name = pid_key.replace("new_", "").replace("_", " ").strip()
            new_topic = Topic(name=clean_name, level=0, is_groomed=1, chain_updated_at=datetime.now())
            session.add(new_topic)
            session.flush()
            new_parents_db[pid_key] = new_topic
            print(f"  Created NEW root: {new_topic.name} (DB ID: {new_topic.id})")
                
    # 2. Process merges
    for n_data in nodes_data:
        raw_node_id = n_data["id"]
        raw_merge_id = n_data.get("merged_into_id")
        # IDs can arrive as int or string-of-int from LLM; coerce both
        try:
            node_id = int(raw_node_id)
        except (TypeError, ValueError):
            continue
        try:
            merge_target_id = int(raw_merge_id) if raw_merge_id is not None else None
        except (TypeError, ValueError):
            merge_target_id = None
        if not (merge_target_id is not None and node_id != merge_target_id):
            continue
        if _would_create_cycle(node_map, node_id, merge_target_id):
            print(f"  SKIPPED merge {node_id} -> {merge_target_id} (would create cycle)")
            continue
        db_node = node_map.get(node_id)
        target_node = node_map.get(merge_target_id)
        if not (db_node and target_node):
            continue
            
        was_leaf = not db_node.children
        
        # Transfer memories (rare — typically only leaf nodes hold memories)
        for mem in db_node.episodic_memories: mem.topic_id = target_node.id
        for mem in db_node.user_memories: mem.topic_id = target_node.id
        for mem in db_node.knowledge_memories: mem.topic_id = target_node.id
        for mem in db_node.decision_memories: mem.topic_id = target_node.id
        # Re-parent children (the common merge operation)
        for child in list(db_node.children):
            if not _would_create_cycle(node_map, child.id, target_node.id):
                child.parent_id = target_node.id
            else:
                print(f"  SKIPPED re-parent child {child.id} -> {target_node.id} (would create cycle)")
        print(f"  Merged '{db_node.name}' (ID {db_node.id}) INTO '{target_node.name}' (ID {target_node.id})")
        
        # Delete old cache entry only if it was a leaf (had a cache entry)
        if was_leaf:
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == db_node.id).delete()
        session.delete(db_node)
        del node_map[node_id]
        target_node.is_groomed = 1
        target_node.chain_updated_at = datetime.now()

    # 3. Update remaining targeted nodes (shifting + renaming + grooming)
    successfully_mapped_nodes = []
    target_ids = {n.id for n in target_nodes}
    for n_data in nodes_data:
        raw_id = n_data["id"]
        try:
            node_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if node_id not in target_ids:
            continue
        
        db_node = node_map.get(node_id)
        if not db_node:
            continue
        
        # Only update if the key is explicitly returned
        if "name" in n_data and n_data["name"] is not None:
            db_node.name = n_data["name"]
        
        db_node.is_groomed = 1
        db_node.chain_updated_at = datetime.now()
        
        if "parent_id" in n_data:
            pid = n_data["parent_id"]
            if isinstance(pid, str):
                pid_key = pid.lower()
                if pid_key in new_parents_db:
                    db_node.parent_id = new_parents_db[pid_key].id
            elif pid is not None:
                try:
                    pid_int = int(pid)
                    if pid_int in node_map and not _would_create_cycle(node_map, node_id, pid_int):
                        db_node.parent_id = node_map[pid_int].id
                    elif pid_int == node_id:
                        print(f"  SKIPPED self-parent for node {node_id}")
                except (TypeError, ValueError):
                    pass
            else:
                db_node.parent_id = None
            
        successfully_mapped_nodes.append(db_node)

    # 3b. Mark ALL surviving target nodes as groomed (LLM omits nodes already okay)
    for tgt in target_nodes:
        if tgt.id in node_map:
            db_tgt = node_map[tgt.id]
            db_tgt.is_groomed = 1
            db_tgt.chain_updated_at = datetime.now()
            if db_tgt not in successfully_mapped_nodes:
                successfully_mapped_nodes.append(db_tgt)

    # 4. Recalculate Levels (BFS from roots)
    session.flush()
    def set_level(node, current_level, visited=None):
        if visited is None: visited = set()
        if node.id in visited: return
        visited.add(node.id)
        node.level = current_level
        for child in node.children:
            set_level(child, current_level + 1, visited)
            
    for r in session.query(Topic).filter(Topic.parent_id.is_(None)).all():
        set_level(r, 0)
        
    # 5. Clean up orphaned empty nodes
    count_deleted = 0
    for node in list(session.query(Topic).all()):
        has_memories = (node.episodic_memories or node.user_memories or 
                        node.knowledge_memories or node.decision_memories)
        if not has_memories and not node.children:
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == node.id).delete()
            session.delete(node)
            count_deleted += 1
    if count_deleted > 0:
        print(f"  Deleted {count_deleted} empty orphaned/duplicate nodes.")
            
    session.flush()

    # 6. Update embedding cache for newly groomed leaf nodes
    update_leaf_embedding_cache(session, successfully_mapped_nodes, embedder)

    session.commit()
    print("  Mapping applied and committed.")


def pass_global_bootstrap(session, full_tree_text, dry_run=True):
    print(" PHASE 1: GLOBAL BOOTSTRAP (SMALL DB)")
    ai = SafeAI()
    embedder = EmbeddingManager()
    
    system_prompt = "You are an expert taxonomist restructuring a messy category tree into a clean, well-organized taxonomy. Output valid JSON."
    
    prompt = f"""
    You are an expert taxonomist. Restructure the messy category graph below into a clean taxonomy.
    
    STRUCTURAL RULES:
    1. STRICT OMISSION: ONLY OUTPUT NODES THAT NEED CHANGES. If a node is fine, omit it entirely. If a specific property (name/parent/merge) doesn't change, DO NOT OUTPUT THAT KEY (no nulls!).
    2. MERGING: If there are exact duplicate nodes across branches, choose one as primary, and set the secondary node's `merged_into_id` to the primary ID.
    4. NO EMPTY WRAPPER ROOTS: Do NOT create artificial top-level wrapper nodes (like "NEW_root_shopping" -> "Shopping"). Instead, elevate existing broad category nodes (like "Shopping") to be the root by setting their `parent_id` to `null`. Only create a `NEW_` root if absolutely no existing node covers the concept.
    5. DEPTH OF TREES SHOULD DEPEND ON THE REQUIREMENT OF THE TREE WHETHER SHORT WILL SUFFICE IF IT WONT THEN ONLY EXTEND TO MAINTAIN THE LOGICAL FLOW OF THE TREE.
    6. NO NAME REPETITION: A child must NEVER have the same name as its parent or grandparent. If "Books" already exists as a parent, do NOT create another "Books" child under it — merge into the existing one.
    7. BREAK CHIMERA CHAINS: If a single branch contains unrelated topics forced together (e.g., "Dog Agility → Environmental Science → Geology"), split them into separate root-level branches. Each root should represent ONE coherent domain.
    8. FLATTEN REDUNDANT WRAPPERS: Remove single-child intermediary nodes that add no semantic value. E.g., "Sleep → Morning Routine → Sleep → Sleep → Morning Routine" should become "Sleep → Morning Routine".
    
    ALL NODES:
    {full_tree_text}
    """
    all_nodes = session.query(Topic).all()
    apply_mapping(session, ai, prompt, system_prompt, all_nodes, embedder, dry_run)


def rebuild_groomed_cache(session, embedder):
    groomed_leafs = session.query(Topic).filter(Topic.is_groomed == 1).filter(~Topic.children.any()).all()
    if not groomed_leafs:
        return np.array([]), []
        
    print(f"\n[Matrix Cache] Validating cache for {len(groomed_leafs)} groomed leaf chains...")
    
    matrix = []
    groomed_ids = []
    
    # Separate stale vs cached leaves
    stale_leaves = []
    
    for i, leaf in enumerate(groomed_leafs):
        cache = session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == leaf.id).first()
        
        needs_update = (not cache) or (
            leaf.chain_updated_at and cache.last_embedded_at and 
            leaf.chain_updated_at > cache.last_embedded_at
        )
            
        if needs_update:
            stale_leaves.append((i, leaf, cache))
            matrix.append(None)  # placeholder
        else:
            matrix.append(np.frombuffer(cache.chain_embedding, dtype=np.float32))
        groomed_ids.append(leaf.id)
    
    # Batch embed all stale leaves at once
    if stale_leaves:
        stale_texts = [build_chain_path_text(leaf) for _, leaf, _ in stale_leaves]
        stale_vecs = embedder.get_batch_embeddings(stale_texts)
        
        for (idx, leaf, cache), vec in zip(stale_leaves, stale_vecs):
            vec_arr = np.array(vec, dtype=np.float32)
            
            if cache:
                vec_bytes = vec_arr.tobytes()
                cache.chain_embedding = vec_bytes
                cache.last_embedded_at = datetime.now()
            else:
                session.add(TopicEmbeddingCache(
                    topic_leaf_id=leaf.id, 
                    chain_embedding=vec_bytes, 
                    last_embedded_at=datetime.now()
                ))
            matrix[idx] = vec_arr
        
        session.flush()
        print(f"  Generated {len(stale_leaves)} new embeddings for the SQL Cache.")
            
    session.commit()
    return np.array(matrix), groomed_ids


def pass_targeted_vector(session, dry_run=True, top_k=3):
    print(" PHASE 2: TARGETED VECTOR GROOMING (MATRIX SCAN)")
    
    ai = SafeAI()
    embedder = EmbeddingManager()
    system_prompt = "You are an expert taxonomist. Integrate messy branches into groomed branches: merge duplicates, break chimera chains, enforce max depth of 4, and eliminate name repetition. Output valid JSON."
    
    # 1. Rebuild and fetch Groomed Matrix (The Haystack)
    groomed_matrix, groomed_ids = rebuild_groomed_cache(session, embedder)
    if len(groomed_ids) == 0:
        print("No groomed nodes exist. Falling back to Global Bootstrap or aborting.")
        return
        
    # 2. Identify Targets (The Needles)
    all_ungroomed = session.query(Topic).filter(Topic.is_groomed == 0).all()
    messy_heads = [
        n for n in all_ungroomed
        if n.parent_id is None or (n.parent and n.parent.is_groomed == 1)
    ]
    if not messy_heads:
        print("No new messy chains to groom.")
        return
        
    print(f"Found {len(messy_heads)} ungroomed subtree heads to groom.")
    
    # 3. Build branch texts and descendants for ALL heads upfront
    head_branch_texts = []
    head_descendants = []
    raw_texts = []
    for mr in messy_heads:
        head_descendants.append(_get_all_descendants(mr))
        b_text = build_branch_text(mr)
        head_branch_texts.append(f"--- MESSY BRANCH {mr.id} ---\n{b_text}")
        raw_texts.append(b_text)
    
    # 4. Batch-embed ALL messy heads in one call
    all_messy_vecs = embedder.get_batch_embeddings(raw_texts)
    target_matrix = np.array(all_messy_vecs, dtype=np.float32)
    
    # 5. Single matrix scan: (M x D) @ (D x N) -> (M x N) similarity scores
    similarity_scores = target_matrix @ groomed_matrix.T
    
    # 6. Per-head: resolve top-K groomed root branches (vectorized)
    #    Pre-build leaf → root lookup (walk ORM once per unique leaf)
    leaf_to_root = {}
    for gid in groomed_ids:
        if gid not in leaf_to_root:
            curr = session.query(Topic).get(gid)
            while curr and curr.parent:
                curr = curr.parent
            leaf_to_root[gid] = curr.id if curr else None

    #    Single argsort across all rows at once
    k = min(top_k, similarity_scores.shape[1])
    top_k_indices = np.argsort(similarity_scores, axis=1)[:, -k:][:, ::-1]

    #    Map indices → root IDs via the lookup
    per_head_groomed_roots = []
    for row_indices in top_k_indices:
        root_ids = {leaf_to_root[groomed_ids[idx]] for idx in row_indices if leaf_to_root.get(groomed_ids[idx]) is not None}
        per_head_groomed_roots.append(root_ids)
    
    # 7. Pre-render groomed branch texts (cache to avoid re-rendering)
    groomed_branch_cache = {}
    all_root_ids = set().union(*per_head_groomed_roots)
    for rid in all_root_ids:
        g_root = session.query(Topic).get(rid)
        groomed_branch_cache[rid] = f"\n--- EXISTING GROOMED BRANCH {g_root.id} ---\n" + build_branch_text(g_root)
    
    # 8. Dynamic token-based batching for LLM calls
    cursor = 0
    while cursor < len(messy_heads):
        batch_messy_texts = []
        batch_groomed_root_ids = set()
        batch_nodes = []
        batch_token_count = 0
        
        while cursor < len(messy_heads):
            # Calculate what adding this head would cost
            candidate_text = head_branch_texts[cursor]
            candidate_groomed_ids = per_head_groomed_roots[cursor]
            new_groomed_ids = candidate_groomed_ids - batch_groomed_root_ids
            new_groomed_tokens = sum(estimate_tokens(groomed_branch_cache[rid]) for rid in new_groomed_ids)
            head_tokens = estimate_tokens(candidate_text)
            added_tokens = head_tokens + new_groomed_tokens
            
            # If batch is non-empty and adding this would exceed limit, stop packing
            if batch_messy_texts and (batch_token_count + added_tokens) > TOKEN_LIMIT:
                break
            
            batch_messy_texts.append(candidate_text)
            batch_groomed_root_ids.update(candidate_groomed_ids)
            batch_nodes.extend(head_descendants[cursor])
            batch_token_count += added_tokens
            cursor += 1
        
        batch_nodes = list(set(batch_nodes))
        
        groomed_context_text = "".join(groomed_branch_cache[rid] for rid in batch_groomed_root_ids)
        
        print(f"\n-> Grooming {len(batch_messy_texts)} messy chains (~{batch_token_count} tokens) against {len(batch_groomed_root_ids)} groomed branches...")
        
        prompt = f"""
        You are an expert taxonomist. Integrate messy branches into the existing groomed tree.
        
        STRUCTURAL RULES:
        1. STRICT OMISSION: ONLY OUTPUT NODES THAT NEED CHANGES. If a node is fine, omit it. If a specific property (name/parent/merge) doesn't change, DO NOT OUTPUT THAT KEY (no nulls!).
        2. DEPTH OF TREES SHOULD DEPEND ON THE REQUIREMENT OF THE TREE WHETHER SHORT WILL SUFFICE IF IT WONT THEN ONLY EXTEND TO MAINTAIN THE LOGICAL FLOW OF THE TREE.
        3. NO NAME REPETITION: Never place a node under a parent with the same or near-identical name. Merge duplicates using `merged_into_id`.
        4. BREAK CHIMERA CHAINS: If a messy branch mixes unrelated topics (e.g., "Healthcare → Music → Dance"), split them — re-parent each topic segment into its correct groomed branch independently.
        5. MERGING: If a messy node duplicates an existing groomed node, set `merged_into_id` to the groomed node's ID.
        6. RE-PARENTING: Place each messy node under the most semantically appropriate existing groomed parent.
        7. FLATTEN: Skip single-child wrapper nodes that add no meaning.
        8. NEW ROOTS: Only if a topic is genuinely new and has no match in groomed branches, set parent_id to a string like "NEW_TopicName".
        
        EXISTING GROOMED BRANCHES (Read-only — do NOT modify these, only reference their IDs as parents or merge targets):
        {groomed_context_text}
        
        NEW MESSY BRANCHES TO GROOM:
        {"".join(batch_messy_texts)}
        """
        
        apply_mapping(session, ai, prompt, system_prompt, batch_nodes, embedder, dry_run)


def reorganize_tree(dry_run=True):
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        all_roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
        full_tree_text = ""
        for r in all_roots:
            full_tree_text += build_branch_text(r)
        total_tokens = estimate_tokens(full_tree_text)
        
        print(f"Total graph tokens estimated: {total_tokens}/{TOKEN_LIMIT}")
        
        if total_tokens < TOKEN_LIMIT:
            pass_global_bootstrap(session, full_tree_text, dry_run=dry_run)
        else:
            pass_targeted_vector(session, dry_run=dry_run)
            
    finally:
        session.close()
        print("\nAll grooming completed successfully.")

if __name__ == "__main__":
    dry_run = False
    reorganize_tree(dry_run=dry_run)
