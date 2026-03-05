import json
import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, TopicEmbeddingCache
from Database.embedder import EmbeddingManager
from Memory_extract.safe_ai import SafeAI
from retrieval.root_descent import BM25Scorer
from pydantic import BaseModel, Field
from typing import List, Union, Optional

class ReorganizedNode(BaseModel):
    id: Union[int, str] = Field(description="Original ID of the node.")
    name: Optional[str] = Field(default=None, description="OMIT THIS KEY if the name is not changing. Only include if renaming the category.")
    parent_id: Optional[Union[int, str]] = Field(default=None, description="OMIT THIS KEY if the parent is not changing. Only include if re-parenting. Use 'NEW_Root' for a new root.")
    merged_into_id: Optional[int] = Field(default=None, description="OMIT THIS KEY if the node is not being merged. Only include if merging into another ID.")

class TreeReorganizationSchema(BaseModel):
    modified_nodes_only: List[ReorganizedNode] = Field(description="ONLY include nodes that require a change (e.g., merging, newly assigned parent, or renamed). Do NOT include nodes that are already correctly placed and need no changes.")


TOKEN_LIMIT = 5000

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
        print(f"JSON Error: {e}\n")
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
    merge_redirects = {}  # old_id -> target_id, for redirecting step-3 references
    nodes_reparented_by_merge = set()  # IDs of children already handled by merge
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
            # Instead of skipping, RESOLVE the cycle. If target is a descendant of source, walk up from target to find the direct child of source that sits on the path. Re-parent that"bridge node" to source's parent, breaking the cycle.
            db_node = node_map.get(node_id)
            target_node = node_map.get(merge_target_id)
            if not (db_node and target_node):
                continue
            
            # Walk from target upward to find bridge (direct child of db_node)
            bridge = target_node
            while bridge and bridge.parent_id != node_id:
                bridge = node_map.get(bridge.parent_id) if bridge.parent_id else None
            
            if bridge:
                # Snip: give bridge its grandparent (db_node's parent)
                bridge.parent = db_node.parent  # Use relationship, not FK — keeps backref in sync
                nodes_reparented_by_merge.add(bridge.id)  # Prevent step 3 from overriding
                print(f"  Resolved cycle: lifted '{bridge.name}' (ID {bridge.id}) out from under '{db_node.name}' (ID {node_id})")
            else:
                # Shouldn't happen, but safety net
                print(f"  SKIPPED merge {node_id} -> {merge_target_id} (cycle could not be resolved)")
                continue
        db_node = node_map.get(node_id)
        target_node = node_map.get(merge_target_id)
        if not (db_node and target_node):
            continue
            
        was_leaf = not db_node.children
        
        # Transfer memories (rare — typically only leaf nodes hold memories)
        if was_leaf:
            for mem in list(db_node.episodic_memories): mem.topic = target_node
            for mem in list(db_node.user_memories): mem.topic = target_node
            for mem in list(db_node.knowledge_memories): mem.topic = target_node
            for mem in list(db_node.decision_memories): mem.topic = target_node
        # Re-parent children (the common merge operation)
        # If child can't go to target (cycle), send to source's parent (grandparent).
        # This guarantees NO child is ever orphaned before source deletion.
        children_list = list(db_node.children)
        print(f"  MERGE: '{db_node.name}' (ID {db_node.id}, parent={db_node.parent_id}) INTO '{target_node.name}' (ID {target_node.id}, parent={target_node.parent_id})")
        print(f"    Children to move: {[(c.name, c.id) for c in children_list]}")
        for child in children_list:
            old_pid = child.parent_id
            if not _would_create_cycle(node_map, child.id, target_node.id):
                # Use relationship attribute (.parent), NOT FK (.parent_id).
                # This automatically updates BOTH sides of the backref:
                #   - removes child from db_node.children
                #   - adds child to target_node.children
                # Prevents stale cache → no backref cascade on delete.
                child.parent = target_node
                print(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} → {target_node.id} (target)")
            else:
                # Fallback: inherit source's parent (grandparent) — always safe
                child.parent = db_node.parent
                print(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} → {db_node.parent_id} (grandparent fallback, cycle)")
            nodes_reparented_by_merge.add(child.id)
        
        # Delete old cache entry only if it was a leaf (had a cache entry)
        if was_leaf:
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == db_node.id).delete()
        session.delete(db_node)
        merge_redirects[node_id] = merge_target_id
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
        
        # Rename if the LLM returned a new name
        if "name" in n_data and n_data["name"] is not None:
            db_node.name = n_data["name"]
        
        db_node.is_groomed = 1
        db_node.chain_updated_at = datetime.now()
        
        # Handle parent_id changes (step 3: shifting/moving nodes)
        if "parent_id" in n_data:
            # If merge already reparented this node, LLM's explicit parent_id overrides it
            is_override = node_id in nodes_reparented_by_merge
            if is_override:
                print(f"  STEP3-OVERRIDE: '{db_node.name}' (ID {node_id}) — merge-reparented, but LLM wants explicit redirect")
            
            pid = n_data["parent_id"]
            old_pid = db_node.parent_id
            if isinstance(pid, str):
                pid_key = pid.lower()
                if pid_key in new_parents_db:
                    db_node.parent = new_parents_db[pid_key]
                    print(f"  STEP3-NEW: '{db_node.name}' (ID {node_id}): parent {old_pid} → {db_node.parent_id} (new root '{pid_key}')")
            elif pid is not None:
                try:
                    pid_int = int(pid)
                    orig_pid = pid_int
                    # Redirect if parent was merged into another node
                    while pid_int in merge_redirects:
                        pid_int = merge_redirects[pid_int]
                    if pid_int != orig_pid:
                        print(f"  STEP3-REDIRECT: '{db_node.name}' (ID {node_id}): parent ref {orig_pid} → {pid_int} (merge redirect)")
                    if pid_int in node_map and not _would_create_cycle(node_map, node_id, pid_int):
                        db_node.parent = node_map[pid_int]
                        print(f"  STEP3-MOVE: '{db_node.name}' (ID {node_id}): parent {old_pid} → {pid_int}")
                    elif pid_int == node_id:
                        print(f"  STEP3-BLOCKED: self-parent for '{db_node.name}' (ID {node_id})")
                    elif pid_int not in node_map:
                        print(f"  STEP3-DANGLING: '{db_node.name}' (ID {node_id}): parent ref {pid_int} not in node_map! Keeping parent={old_pid}")
                except (TypeError, ValueError):
                    pass
            elif not is_override:
                # Only allow making root if NOT a merge-override (prevent accidental root elevation)
                db_node.parent = None
                print(f"  STEP3-ROOT: '{db_node.name}' (ID {node_id}): parent {old_pid} → NULL (elevated to root)")
            
        successfully_mapped_nodes.append(db_node)
    
    # Flush step 3 parent changes before orphan cleanup
    session.flush()

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
    session.expire_all()  # Ensure newly-elevated roots (parent_id=NULL) are picked up
    def set_level(node, current_level, visited=None):
        if visited is None: visited = set()
        if node.id in visited: return
        visited.add(node.id)
        node.level = current_level
        for child in node.children:
            set_level(child, current_level + 1, visited)
            
    for r in session.query(Topic).filter(Topic.parent_id.is_(None)).all():
        set_level(r, 0)
        
    # 5. Clean up orphaned empty nodes (loop to cascade: deleting a leaf may make its parent empty)
    session.expire_all()  # Force SQLAlchemy to re-read relationships from DB
    total_deleted = 0
    while True:
        count_deleted = 0
        for node in list(session.query(Topic).all()):
            has_memories = (node.episodic_memories or node.user_memories or 
                            node.knowledge_memories or node.decision_memories)
            if not has_memories and not node.children:
                print(f"  CLEANUP-DELETE: '{node.name}' (ID {node.id}, parent={node.parent_id}) — empty, no children")
                session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == node.id).delete()
                session.delete(node)
                count_deleted += 1
        if count_deleted == 0:
            break
        session.flush()
        session.expire_all()
        total_deleted += count_deleted
    if total_deleted > 0:
        print(f"  Deleted {total_deleted} empty orphaned/duplicate nodes.")
             
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
    You are an expert taxonomist. Restructure the messy category graph below into a clean, multi-rooted taxonomy.
    
    HOW TO MAKE CHANGES:
    - To RENAME a node: output {{"id": <existing_id>, "name": "New Name"}}
    - To MOVE a node under a different parent: output {{"id": <existing_id>, "parent_id": <new_parent_id>}}
    - To MAKE a node a root: output {{"id": <existing_id>, "parent_id": null}}. Use a specific descriptive name (rename the node if needed). NEVER use generic names like "root", "general", "misc", or "other".
    - To MERGE duplicates: output {{"id": <duplicate_id>, "merged_into_id": <primary_id>}}
      NOTE: Merges automatically re-parent all children of the deleted node under the target.
      HOWEVER — if a child does NOT semantically belong under the merge target, you MUST output a SEPARATE parent_id change for that child to redirect it to the correct parent.
      Example: Merging duplicate "Food & Recipes" moves child "Cartagena, Colombia" under Food — but Cartagena is a location, so output: {{"id": <cartagena_id>, "parent_id": <travel_root_id>}} to redirect it.
    - ALWAYS prefer using parent_id: null to elevate an existing node to root. Do NOT use NEW_ prefixes — there is almost never a need to create a brand new node when you can elevate an existing one.
    
    MANDATORY FIXES (do these FIRST):
    A. If ANY node is named "root", "general", "misc", or "other" — either RENAME it or ELEVATE its children to be independent roots and let it be deleted as empty.
    B. If the tree has a single mega-root with many unrelated domain children — ELEVATE each domain child to root by setting its parent_id to null. Do NOT create NEW_ roots for concepts that already exist as nodes in the tree! Use their existing IDs.
    C. SCAN ALL root-level branches. If a root has fewer than 4 nodes total (including children), it is likely misplaced. Move the ENTIRE small branch under the most relevant larger root. Examples:
       - "Bali" with children "Mount Batur" and "Uluwatu" → move under "Travel" → "International Travel Destinations"
       - "Futures Studies" → move under "Education" or "Research"  
       - "Fine Arts" → merge with or move under "Entertainment & Media"
       - A lone leaf root with memories should NEVER remain a root — always place it under a domain.
    D. CHECK for nodes under the WRONG parent. Examples:
       - "Forex Brokers" and "Liquidity Providers" under "Business & Management" should be under "Personal Finance" → "Financial Markets"
       - "Analytics" under "Business & Management" should be under "Marketing" or "Technology"
    
    STRUCTURAL RULES:
    1. ROOTS = DISTINCT DOMAINS: Each root should represent a topic that is fundamentally different from every other root — so different that a similarity search would never confuse them. "Travel" and "Career" are clearly distinct → separate roots. "Society" and "Culture" are too similar → merge into one. If a small root overlaps with a larger root, absorb it. Examples:
       - "Society" (Feminism, Gender Norms) → absorb into "Culture"
       - "Political Science & Governance" (1 branch) → absorb into "Culture"
       - "Personal Development" (Reading, Volunteering) → absorb into "Productivity" or "Education"
       - "Family Relationships" (Child Activities) → absorb into "Culture" or "Health & Wellness"
    2. MERGE DUPLICATES: Keep one, set the other's merged_into_id.
    3. NATURAL DEPTH: Use as many levels as needed to organize information clearly — don't flatten OR pad. If a topic naturally has sub-sub-topics (e.g., Travel → International → Japan → Kichijoji), let it be deep. If a topic is simple (e.g., Literature → Book Recommendations), keep it shallow. Don't leave 20+ children under one parent — group them into meaningful sub-categories.
    4. NO NAME REPETITION: A child must never share its name with its parent or any ancestor. If found, FLATTEN by moving the child's children up to the parent and deleting the duplicate-named child.
    5. BREAK CHIMERA CHAINS: Split branches that mix unrelated topics.
    6. FLATTEN WRAPPERS: Remove single-child intermediary nodes with no semantic value.
    7. COHERENT GROUPING: "Technology" and "Travel" should NOT be siblings under "Sustainable Living" — they are different domains.
    8. CONSOLIDATE OVERLAPPING ROOTS: If two roots cover overlapping concepts, keep the broader one and absorb the other. But keep genuinely distinct domains as separate roots even if they are small — a small root that is semantically unique (e.g., "Spirituality") is fine.
    9. SEMANTIC ACCURACY: Every node must be under a semantically correct parent. "Yoga" is NOT a "Team Sport" — it belongs under "Health & Wellness". Double-check every parent-child relationship makes logical sense.
    
    OUTPUT RULES:
    10. Only output nodes that NEED changes. Omit nodes that are already correct.
    
    IMPORTANT: To make a node a root, set parent_id: null. Do NOT create NEW_ roots — elevate existing nodes instead. NEVER create a root called "root", "general", or similar generic names.
    
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
            vec_bytes = vec_arr.tobytes()
            if cache:
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
    print(" PHASE 2: TARGETED VECTOR GROOMING (HYBRID SCAN)")
    
    ai = SafeAI()
    embedder = EmbeddingManager()
    system_prompt = "You are an expert taxonomist. Integrate messy branches into groomed branches: merge duplicates, break chimera chains, fix misplacements, and eliminate name repetition. Output valid JSON."
    
    # 1. Rebuild and fetch Groomed Matrix (The Haystack)
    groomed_matrix, groomed_ids = rebuild_groomed_cache(session, embedder)
    if len(groomed_ids) == 0:
        print("No groomed nodes exist. Falling back to Global Bootstrap or aborting.")
        return

    # Build groomed chain texts for BM25 corpus
    groomed_chain_texts = []
    for gid in groomed_ids:
        leaf = session.query(Topic).get(gid)
        groomed_chain_texts.append(build_chain_path_text(leaf))
        
    # 2. Identify Targets: ALL leaf nodes that are ungroomed OR have ungroomed ancestors
    #    Also include groomed nodes so the LLM can fix existing issues
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
        head_branch_texts.append(f"--- NEW MESSY BRANCH {mr.id} ---\n{b_text}")
        raw_texts.append(b_text)
    
    # 4. Batch-embed ALL messy heads in one call
    all_messy_vecs = embedder.get_batch_embeddings(raw_texts)
    target_matrix = np.array(all_messy_vecs, dtype=np.float32)
    
    # 5. Hybrid scoring: vector similarity (0.6) + BM25 keyword match (0.4)
    vector_scores = target_matrix @ groomed_matrix.T  # (M x N)
    
    # BM25 scoring for each messy head against groomed chain texts
    bm25 = BM25Scorer()
    bm25.fit(groomed_chain_texts)
    
    bm25_scores = np.zeros_like(vector_scores)
    VECTOR_THRESHOLD = 0.3  # Only BM25-score candidates above this vector similarity
    for i, raw_text in enumerate(raw_texts):
        candidates = np.where(vector_scores[i] > VECTOR_THRESHOLD)[0]
        for j in candidates:
            bm25_scores[i, j] = bm25.score(raw_text, j)
    
    # Normalize BM25 scores to [0, 1] range per row for fair combination
    for i in range(bm25_scores.shape[0]):
        row_max = bm25_scores[i].max()
        if row_max > 0:
            bm25_scores[i] /= row_max
    
    # Combined: 60% vector + 40% BM25 (same weights as retrieval pipeline)
    similarity_scores = (vector_scores * 0.6) + (bm25_scores * 0.4)
    
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
        groomed_branch_cache[rid] = f"\n--- GROOMED BRANCH {g_root.id} [ESTABLISHED] ---\n" + build_branch_text(g_root)
    
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
        
        # Include groomed nodes in target_nodes so the LLM can fix them too
        groomed_nodes_in_batch = []
        for rid in batch_groomed_root_ids:
            g_root = session.query(Topic).get(rid)
            groomed_nodes_in_batch.extend(_get_all_descendants(g_root))
        batch_nodes = list(set(batch_nodes + groomed_nodes_in_batch))
        
        groomed_context_text = "".join(groomed_branch_cache[rid] for rid in batch_groomed_root_ids)
        
        print(f"\n-> Grooming {len(batch_messy_texts)} messy chains (~{batch_token_count} tokens) against {len(batch_groomed_root_ids)} groomed branches...")
        
        prompt = f"""
        You are an expert taxonomist. Integrate new messy branches into the existing groomed tree.
        
        CONTEXT: Branches marked [ESTABLISHED] are already organized. Branches marked [NEW MESSY] are new and need integration.
        You may ALSO fix issues in [ESTABLISHED] branches if you spot misplacements, duplicates, or name repetitions.
        
        HOW TO MAKE CHANGES:
        - To MOVE a node under a different parent: output {{"id": <existing_id>, "parent_id": <new_parent_id>}}
        - To MERGE duplicates: output {{"id": <duplicate_id>, "merged_into_id": <primary_id>}}
          NOTE: Merges automatically re-parent all children of the deleted node under the target.
          HOWEVER — if a child does NOT semantically belong under the merge target, you MUST output a SEPARATE parent_id change for that child to redirect it to the correct parent.
        - To RENAME: output {{"id": <existing_id>, "name": "New Name"}}
        - To make a node a root: output {{"id": <existing_id>, "parent_id": null}}
        
        HOW TO PROCESS NEW MESSY BRANCHES:
        1. Look at ONLY the leaf nodes (nodes with memories) in each messy branch.
        2. For each leaf, find the most semantically appropriate EXISTING groomed parent node by its ID.
        3. Re-parent the leaf DIRECTLY under that groomed parent — do NOT preserve the messy chain structure.
        4. The empty intermediate nodes (those without memories) will be auto-deleted by the system.
        
        CRITICAL RULES:
        1. BREAK CHIMERA CHAINS: Messy branches contain chains of unrelated topics. NEVER preserve these chains. Extract each leaf independently and place it under its correct groomed parent.
        2. MERGE DUPLICATES: If a messy leaf duplicates an existing groomed node (same concept), set merged_into_id to the groomed node's ID. Also merge same-name siblings under the same parent.
        3. USE EXISTING GROOMED IDS: Always re-parent into existing groomed branch IDs. Do NOT create new intermediate nodes that duplicate existing names.
        4. NO ORPHAN LEAVES: Every leaf must be placed under a meaningful parent.
        5. NATURAL DEPTH: Use as many levels as needed — don't flatten OR pad artificially.
        6. NO NAME REPETITION: A child must never share its name with its parent or any ancestor. If found, FLATTEN by moving the child's children up to the parent.
        7. SEMANTIC ACCURACY: Every node must be under a semantically correct parent. "Yoga" is NOT a "Team Sport". "Cartagena, Colombia" is NOT under "Food & Recipes".
        8. FIX ESTABLISHED ISSUES: If you see problems in [ESTABLISHED] branches (misplaced nodes, duplicates, name repetition), fix them too.
        
        OUTPUT RULES:
        9. Only output nodes that NEED changes. Omit nodes that are already correct.
        10. Output leaf nodes (with memories) that need re-parenting or merging. Do NOT output empty intermediate nodes.
        
        EXISTING GROOMED BRANCHES [ESTABLISHED] (use these IDs as parent_id or merged_into_id targets):
        {groomed_context_text}
        
        NEW MESSY BRANCHES TO INTEGRATE (extract leaves and place them correctly):
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
