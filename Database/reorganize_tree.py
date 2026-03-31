from torch import chunk
import json
import os
import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, TopicEmbeddingCache
from Database.embedder import EmbeddingManager
from Memory_extract.safe_ai import SafeAI
from retrieval.root_descent import BM25Scorer
from pydantic import BaseModel, Field
from typing import List, Union, Optional
from collections import defaultdict

ROOT_ORTHO_STATE_FILE = os.path.join(os.path.dirname(__file__), ".root_orthogonality_state.json")

class ReorganizedNode(BaseModel):
    id: Union[int, str] = Field(description="Original ID of the node.")
    name: Optional[str] = Field(default=None, description="OMIT THIS KEY if the name is not changing. Only include if renaming the category.")
    parent_id: Optional[Union[int, str]] = Field(default=None, description="OMIT THIS KEY if the parent is not changing. Only include if re-parenting. Use 'NEW_Root' for a new root.")
    merged_into_id: Optional[int] = Field(default=None, description="OMIT THIS KEY if the node is not being merged. Only include if merging into another ID.")

class TreeReorganizationSchema(BaseModel):
    modified_nodes_only: List[ReorganizedNode] = Field(description="ONLY include nodes that require a change (e.g., merging, newly assigned parent, or renamed). Do NOT include nodes that are already correctly placed and need no changes.")

class SubCategory(BaseModel):
    name: str = Field(description="Name of the sub-category.")
    memory_ids: List[int] = Field(description="List of memory IDs that belong to this sub-category.")
    placement: str = Field(description="'child' if sub-categories should be children of the current node, or 'sibling' if they should replace the current node at the same level (as siblings to it under its parent).")

class LeafSplitSchema(BaseModel):
    sub_categories: List[SubCategory] = Field(description="List of sub-categories to split the overloaded leaf into. Each memory ID must appear in exactly one sub-category.")

TOKEN_LIMIT = 30000

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def build_branch_text(node, depth=0, lines=None, children_map=None):
    if lines is None:
        lines = []

    if children_map is not None:
        children = children_map.get(node.id, [])
    else:
        children = node.children

    child_count = len(children)
    prefix = "\t" * depth
    lines.append(f"{prefix}{node.id}: {node.name} ({child_count})")

    for child in children:
        build_branch_text(child, depth + 1, lines, children_map)
    if depth == 0:
        return "\n".join(lines)

def build_chain_path_text(node, parent_map=None) -> str:
    """Builds root > ... > leaf path text for embedding. Uses ORM parent relationship or in-memory map."""
    curr = node
    path = []
    while curr:
        path.append(curr.name)
        if parent_map is not None:
            curr = parent_map.get(curr.id)
        else:
            curr = curr.parent
    path.reverse()
    path_str = " > ".join(path)
    desc = f": {node.description}" if getattr(node, 'description', None) else ""
    return f"{path_str}{desc}"

def _get_all_descendants(node, children_map=None):
    """Recursively collects a node and all its descendants."""
    result = []
    stack = [node]
    while stack:
        curr = stack.pop()
        result.append(curr)
        if children_map is not None:
            stack.extend(children_map.get(curr.id, []))
        else:
            stack.extend(curr.children)

    return result

def _would_create_cycle(node_map, child_id, proposed_parent_id):
    """Walk ancestors of proposed_parent_id; if we hit child_id it's a cycle."""
    visited = set()
    curr_id = proposed_parent_id
    while curr_id is not None:
        if curr_id == child_id or curr_id in visited:
            return True
        visited.add(curr_id)
        curr_node = node_map.get(curr_id)
        curr_id = curr_node.parent_id if curr_node else None
    return False

def _load_last_root_count():
    try:
        with open(ROOT_ORTHO_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("root_count")
    except Exception:
        return None

def _save_last_root_count(root_count):
    try:
        with open(ROOT_ORTHO_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"root_count": root_count}, f)
    except Exception as e:
        print(f"[Root Orthogonality] Warning: failed to persist root count: {e}")

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

def apply_mapping(session, ai, prompt, system_prompt, target_nodes, children_map=None, node_map=None):
    raw_json = ai.generate(prompt=prompt, system_prompt=system_prompt, json_schema=TreeReorganizationSchema.model_json_schema())
    if not raw_json:
        print("Failed to get LLM response.")
        return
    try:
        data = json.loads(raw_json)
        nodes_data = data.get("modified_nodes_only", [])
    except json.JSONDecodeError as e:
        print(f"JSON Error: {e}\n")
        print("Attempting to repair truncated JSON...")
        try:
            # Find the last properly closed JSON object "}"
            last_brace = raw_json.rfind('}')
            if last_brace != -1:
                # Truncate and close out the JSON array and root object
                repaired_json = raw_json[:last_brace+1] + "\n]}"
                data = json.loads(repaired_json)
                nodes_data = data.get("modified_nodes_only", [])
                print(f"Successfully rescued {len(nodes_data)} node changes from truncated output.")
            else:
                print("Could not find a valid object brace to repair.")
                return
        except Exception as repair_e:
            print(f"Repair failed: {repair_e}\n")
            return
    auto_adjusted_node_ids = set()  # internal safety moves (not explicit LLM intent)
    
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
                bridge.parent = db_node.parent
                nodes_reparented_by_merge.add(bridge.id)
                print(f"  Resolved cycle: lifted '{bridge.name}' (ID {bridge.id}) out from under '{db_node.name}' (ID {node_id})")
            else:
                print(f"  SKIPPED merge {node_id} -> {merge_target_id} (cycle could not be resolved)")
                continue
        db_node = node_map.get(node_id)
        target_node = node_map.get(merge_target_id)
        if not (db_node and target_node):
            continue
        
        # Transfer ALL memories (a node might have gained children and ceased being a leaf, but still holds old memories)
        for mem in list(db_node.episodic_memories): mem.topic = target_node
        for mem in list(db_node.user_memories): mem.topic = target_node
        for mem in list(db_node.knowledge_memories): mem.topic = target_node
        for mem in list(db_node.decision_memories): mem.topic = target_node

        children_list = list(db_node.children)
        print(f"  MERGE: '{db_node.name}' (ID {db_node.id}, parent={db_node.parent_id}) INTO '{target_node.name}' (ID {target_node.id}, parent={target_node.parent_id})")
        print(f"    Children to move: {[(c.name, c.id) for c in children_list]}")
        for child in children_list:
            old_pid = child.parent_id
            if not _would_create_cycle(node_map, child.id, target_node.id):
                child.parent = target_node
                print(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} → {target_node.id} (target)")
            else:
                # Fallback: inherit source's parent (grandparent) — always safe
                if db_node.parent is not None:
                    child.parent = db_node.parent
                else:
                    child.parent = target_node # very exceptional case creating a cycle
                auto_adjusted_node_ids.add(child.id)
                print(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} → {db_node.parent_id} (grandparent fallback, cycle)")
            nodes_reparented_by_merge.add(child.id)
        
        # Delete old cache entry
        session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == db_node.id).delete()
        # Clean up ALL in-memory maps before deleting
        if children_map and db_node.parent_id in children_map:
            children_map[db_node.parent_id] = [c for c in children_map[db_node.parent_id] if c.id != node_id]
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

    # 4. Sibling Dedup: merge same-name children under the same parent BEFORE empty node cleanup
    session.expire_all()
    sibling_merges = 0
    for pid, children in children_map.items():
        # Group by lowercase name
        name_groups = defaultdict(list)
        for s in children:
            key = s.name.lower()
            name_groups[key].append(s)
        
        for _, group in name_groups.items():
            if len(group) < 2:
                continue
            # Sort by total memory count descending — keep the richest
            def mem_count(n):
                return len(n.episodic_memories) + len(n.user_memories) + len(n.knowledge_memories) + len(n.decision_memories)
            group.sort(key=mem_count, reverse=True)
            survivor = group[0]
            
            # Ensure survivor's cache gets rebuilt
            if survivor not in successfully_mapped_nodes:
                successfully_mapped_nodes.append(survivor)
                
            for dup in group[1:]:
                print(f"  SIBLING-DEDUP: '{dup.name}' (ID {dup.id}) INTO '{survivor.name}' (ID {survivor.id}) under parent={pid}")
                # Transfer children
                for child in list(dup.children):
                    child.parent = survivor
                # Transfer memories via relationship
                for mem in list(dup.episodic_memories): mem.topic = survivor
                for mem in list(dup.user_memories): mem.topic = survivor
                for mem in list(dup.knowledge_memories): mem.topic = survivor
                for mem in list(dup.decision_memories): mem.topic = survivor
                # Clean up cache, ALL maps, and delete
                session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == dup.id).delete()
                if children_map and pid in children_map:
                    children_map[pid] = [c for c in children_map[pid] if c.id != dup.id]
                if node_map and dup.id in node_map:
                    del node_map[dup.id]
                session.delete(dup)
                sibling_merges += 1
    if sibling_merges > 0:
        session.flush()
        print(f"  Merged {sibling_merges} same-name sibling duplicates.")

    # 5. Enforce Leaf Node Constraint for Memories
    session.expire_all()
    leaf_enforcement_count = 0
    for node in list(session.query(Topic).all()):
        has_memories = (node.episodic_memories or node.user_memories or 
                        node.knowledge_memories or node.decision_memories)
        
        # If it has children AND holds memories, we must split it
        if node.children and has_memories:
            original_name = node.name
            if not original_name.startswith("General "):
                # Create a NEW parent node to take its place in the hierarchy
                new_parent = Topic(
                    name=original_name,
                    level=node.level,
                    parent=node.parent,
                    timestamp=node.timestamp
                )
                session.add(new_parent)
                print(f"  LEAF-ENFORCEMENT: Split '{original_name}' (ID {node.id}). Created new parent .")
                # Move all children of the current node to the new parent
                for child in list(node.children):
                    child.parent = new_parent
                # The current node (holding the memories) stays in its original place as a sibling 
                # to the new parent, but is renamed to serve as the generic leaf bucket.
                node.name = f"General {original_name}"
                node.parent = new_parent
                node.level = new_parent.level + 1
            else:
                parent = node.parent
                level = max(0, node.level - 1)
                if parent is None:
                    parent = Topic(
                        name=original_name.removeprefix("General "),
                        level=level,
                        parent=None,
                        timestamp=node.timestamp
                    )
                    session.add(parent)
                for child in list(node.children):
                    child.parent = parent
                node.parent = parent
                node.level = level + 1                
                # The current node is now a leaf and its name changed. Queue it for cache update.
            if node not in successfully_mapped_nodes:
                    successfully_mapped_nodes.append(node)       
            leaf_enforcement_count += 1

    if leaf_enforcement_count > 0:
        session.flush()
        print(f"  Enforced leaf constraint on {leaf_enforcement_count} nodes (memories moved to generic children).")

    # 6. Clean up orphaned empty nodes (loop to cascade: deleting a leaf may make its parent empty)
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
                # Clean up ALL in-memory maps
                if children_map and node.parent_id in children_map:
                    children_map[node.parent_id] = [c for c in children_map[node.parent_id] if c.id != node.id]
                if node_map and node.id in node_map:
                    del node_map[node.id]
                session.delete(node)
                count_deleted += 1
        if count_deleted == 0:
            break
        session.flush()
        session.expire_all()
        total_deleted += count_deleted
    if total_deleted > 0:
        print(f"  Deleted {total_deleted} empty orphaned/duplicate nodes.")
    
    # 7. Mark ALL surviving target nodes as groomed (LLM omits nodes already okay)
    for tgt in target_nodes:
        if tgt.id in node_map:
            db_tgt = node_map[tgt.id]
            db_tgt.is_groomed = 1
            db_tgt.chain_updated_at = datetime.now()
            if db_tgt not in successfully_mapped_nodes:
                successfully_mapped_nodes.append(db_tgt)

    # 8. Nodes touched by automatic cycle handling are code-adjusted, not LLM-groomed.
    if auto_adjusted_node_ids:
        for auto_id in auto_adjusted_node_ids:
            auto_node = node_map.get(auto_id) or session.get(Topic, auto_id)
            if not auto_node:
                continue
            auto_node.is_groomed = 0
            auto_node.chain_updated_at = datetime.now()

    # 9. Recalculate Levels (BFS from roots) after final structural cleanup.
    session.flush()
    session.expire_all()
    def set_level(node, current_level, visited=None):
        if visited is None: visited = set()
        if node.id in visited: return
        visited.add(node.id)
        node.level = current_level
        for child in node.children:
            set_level(child, current_level + 1, visited)

    for r in session.query(Topic).filter(Topic.parent_id.is_(None)).all():
        set_level(r, 0)

    session.commit()
    session.expire_all()
    print("  Mapping applied and committed.")
    return successfully_mapped_nodes


def pass_root_orthogonality(session, children_map=None, node_map=None):
    """Phase 0: Focused root orthogonality pass. Sends ONLY roots + their descriptions to the LLM."""
    print(" PHASE 0: ROOT ORTHOGONALITY CHECK")
    ai = SafeAI()
    
    # Build lightweight root-only context: root name + description + optional direct child names for disambiguation
    all_roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
    lines = []
    for root in all_roots:
        desc = (root.description or "").strip()
        desc_block = f"description: {desc}" if desc else "description: (none)"
        lines.append(f"ROOT {root.id}: {root.name} | {desc_block}")
    
    root_tree_text = "\n".join(lines)
    root_tokens = estimate_tokens(root_tree_text)
    print(f"  Root tree tokens: ~{root_tokens}")
    
    system_prompt = "You are an expert knowledge architect. Your ONLY job is to consolidate nearly identical or clearly nested root domains. Output valid JSON."
    
    prompt = f"""
    You are an expert knowledge architect. Below are the ROOT DOMAINS of a knowledge tree with their descriptions and a small child-name summary.
    
    YOUR ONLY TASK: Merge roots only when they are clearly redundant or one is a true subset of the other.
    
    HOW TO MAKE CHANGES:
    - To MERGE overlapping roots: output {{"id": <absorbed_root_id>, "merged_into_id": <surviving_root_id>}}
      The absorbed root becomes a child of the surviving root. All its children move with it automatically by the code.
    - To MOVE a root UNDER another root: output {{"id": <child_root_id>, "parent_id": <parent_root_id>}}
    - To RENAME a root: output {{"id": <root_id>, "name": "Better Name"}}
    
    CRITICAL RULES:

     0. MERGE THRESHOLD (STRICT): Only merge roots for one of these cases:
         - exact duplicate / same concept
         - obvious subset of a broader root
         - same user-intent domain where both roots would answer the same query
         Do NOT merge just because two roots are vaguely related.
    
     1. DO NOT MERGE ACROSS DIFFERENT INTENT TYPES:
         - belief system vs celestial mechanics
         - science vs education
         - books vs education
         - abstract worldview vs practical domain
         If the user would search them for different reasons, keep them separate.

     2. RENAME AFTER MERGE (MANDATORY): When you merge domains that go beyond the surviving root's original name,
       you MUST also output a rename for the surviving root to encompass ALL absorbed domains.
       - Example: If you merge "Religion" and "Astrology" into "Philosophy", you MUST rename "Philosophy" to 
         "Philosophy & Belief Systems" or "Worldview & Beliefs" — something that covers all absorbed content.
       - The point: the surviving root's name must still make sense as a retrieval label for ALL its content.
    
     3. DO NOT OVER-MERGE: Query overlap alone is not enough. Merge only when one root is clearly redundant or nested.
         - "Research Ethics" and "Research Methods" → MAY MERGE only if the descriptions show they are not distinct intent domains.
         - "Home Decor" and "Home Maintenance" → MAY MERGE only if their descriptions show near-total overlap.
         - "Religion" and "Philosophy" → DO NOT MERGE.
         - "Astrology" and "Esoteric Beliefs" → DO NOT MERGE unless the descriptions explicitly show they are the same taxonomy bucket.
         - "Science" and "Education" → DO NOT MERGE.
         - "Books" and "Education" → DO NOT MERGE.
    
     4. NICHE SUBSET MERGE: A tiny niche root that is clearly a subset of a broader root should be absorbed.
       - "Space Exploration" with only a few children → merge under "Science"
       - "Mathematics" is a standalone domain → keep separate UNLESS it only has 1-2 children
    
     5. IGNORE STRUCTURAL GENERAL NODES: Do NOT merge "General [Topic]" into "[Topic]".
    
     6. OUTPUT ONLY CHANGED NODES. Do NOT re-output roots that are already correct.
       If no changes are needed, output: {{"modified_nodes_only": []}}
    
     CURRENT ROOTS WITH DESCRIPTIONS:
    {root_tree_text}
    """
    
    all_nodes = session.query(Topic).all()
    result = apply_mapping(session, ai, prompt, system_prompt, all_nodes, children_map=children_map, node_map=node_map)
    print(" ROOT ORTHOGONALITY PASS COMPLETE.\n")
    return result


def pass_global_bootstrap(session, full_tree_text, children_map=None, node_map=None):
    print(" PHASE 1: GLOBAL BOOTSTRAP (SMALL DB)")
    ai = SafeAI()
    
    system_prompt = "You are an expert knowledge architect optimizing a messy category tree into a clean, redundancy-free knowledge graph for AI retrieval. Output valid JSON."
    
    prompt = f"""
    You are an expert knowledge architect. Restructure the messy category graph below into a clean, multi-rooted knowledge tree optimized for semantic retrieval.
    
    HOW TO MAKE CHANGES:
    - To RENAME a node: output {{"id": <existing_id>, "name": "New Name"}}
    - To MOVE a node under a different parent: output {{"id": <existing_id>, "parent_id": <new_parent_id>}}
    - To MAKE a node a root: output {{"id": <existing_id>, "parent_id": null}}. NEVER use generic names like "root", "general", "misc", or "other".
    - To MERGE duplicates: output {{"id": <duplicate_id>, "merged_into_id": <primary_id>}}
      NOTE: Merges automatically re-parent all children of the deleted node.
      HOWEVER — if a child does NOT semantically belong under the merge target, output a SEPARATE parent_id change to redirect it.
    - DO NOT elevate nodes to root (parent_id: null) or create new roots (NEW_ prefix). Root structure is already finalized.
    
    MANDATORY FIXES:
    A. DO NOT BREAK EXISTING ROOTS OR HIERARCHIES: Do not elevate nodes to roots unless absolutely necessary. Maintain the existing root domains (e.g. keep "Literature" and "Music" under "Arts & Entertainment" if they are already there). 
    B. IGNORE STRUCTURAL GENERAL NODES (CRITICAL): You will see many leaf nodes named "General [Topic]" sitting under a parent named "[Topic]" (e.g. "General Business" under "Business").
       - DO NOT MERGE THESE! They are structurally required placeholder nodes that hold memories belonging to the parent concept.
       - NEVER merge "General [Topic]" into "[Topic]". Leave them exactly where they are.
    C. ELIMINATE SCATTERED REDUNDANCY (CRITICAL): If the same concept (e.g., "Rome") appears in multiple places across a single domain, MERGE them or structure them hierarchically. 
       - DO NOT leave redundant nodes like "Italy", "Rome", "Europe Trip Planning" scattered as siblings or disjointed roots.
       - INSTEAD, build a proper knowledge tree hierarchy: "Europe Trip Planning" -> "Italy" -> "Rome" -> "Vatican". 
       - Move related geographical or conceptual sub-topics properly under their broader parent nodes to make retrieval easy.
    
    STRUCTURAL RULES & CONSTRAINTS:
    1. ROOT ORTHOGONALITY (CRITICAL): Roots must be mutually exclusive domains to make retrieval easy. If two roots overlap heavily, merge them.
    2. SIBLING ORTHOGONALITY (CRITICAL): Siblings must be mutually exclusive partitions, NOT semantic variations. 
       - Example (Bad): "Personal Experience", "Personal Opinions", "Personal Preferences". These overlap and break retrieval.
       - Example (Good): "Subjective Feedback", "Objective Information". 
       - If you see siblings with semantic overlap, MERGE them into a single node. Ask: "If I remove this node, what queries become impossible to classify?" If another sibling would catch it, merge them.
    3. MERGE DUPLICATES IMMEDIATELY: Redundancy destroys retrieval. If two nodes represent the exact same concept, merge them.
    4. FIT INTO EXISTING CONCEPTUAL PATHS (CONSTRAINED POWER): You are organizing the user's existing mental model, NOT replacing it with an ontologically rigid library catalog. Fix redundancy and orthogonality, but do NOT arbitrarily shift an established node from one Root Domain to a completely different Root Domain over ontological disagreements.
    5. RETRIEVAL-FRIENDLY HIERARCHY: Build a logical "knowledge tree" where the path from root to leaf makes sense for someone querying for information. Don't flatten natural hierarchies (Region -> Country -> City). Let it nest naturally.
    6. NO NAME REPETITION: A child must never share its name with its parent or any ancestor. If found, FLATTEN.
    7. FLATTEN POINTLESS WRAPPERS: Remove single-child intermediary nodes with no semantic value, UNLESS they hold important hierarchical meaning (like a Country node).
    
    OUTPUT RULES:
    8. STRICT OUTPUT LIMIT: Only output nodes that NEED changes. Omit any node that is already correct.
       - DO NOT RE-OUTPUT THE ENTIRE TREE. If you return nodes that you did not change, you will crash the system due to token limits.
       - Your output must be a concise JSON array of ONLY the specific nodes whose name, parent_id, or merged_into_id you actively modified.
    
    IMPORTANT: Do NOT create new root nodes. Root structure is already finalized by Phase 0.
    
    ALL NODES:
    {full_tree_text}
    """
    all_nodes = session.query(Topic).all()
    return apply_mapping(session, ai, prompt, system_prompt, all_nodes, children_map=children_map, node_map=node_map)


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


def pass_targeted_vector(session, full_tree_text, top_k=3, parent_map=None, children_map=None, node_map=None):
    print(" PHASE 2: TARGETED VECTOR GROOMING (HYBRID SCAN)")
    
    ai = SafeAI()
    embedder = EmbeddingManager()
    system_prompt = "You are an expert taxonomist. Integrate messy branches into groomed branches: merge duplicates, break chimera chains, fix misplacements, and eliminate name repetition. Output valid JSON."
    
    # 1. Identify Targets: ALL leaf nodes that are ungroomed OR have ungroomed ancestors and groomed nodes so the LLM can fix existing issues
    all_ungroomed = session.query(Topic).filter(Topic.is_groomed == 0).all()
    messy_heads = [
        n for n in all_ungroomed
        if n.parent_id is None or (n.parent and n.parent.is_groomed == 1)
    ]
    if not messy_heads:
        print("No new messy chains to groom.")
        return
        
    print(f"Found {len(messy_heads)} ungroomed subtree heads to groom.")
    
    # 2. Build branch texts and descendants for ALL heads upfront
    head_branch_texts = []
    head_descendants = []
    raw_texts = []
    for mr in messy_heads:
        head_descendants.append(_get_all_descendants(mr, children_map=children_map))
        b_text = build_branch_text(mr, children_map=children_map)
        head_branch_texts.append(f"MESSY BRANCH {mr.id}\n{b_text}")
        raw_texts.append(b_text)
    
    # 3. Batch-embed ALL messy heads in one call
    all_messy_vecs = embedder.get_batch_embeddings(raw_texts)
    target_matrix = np.array(all_messy_vecs, dtype=np.float32)

    # 4. Rebuild and fetch Groomed Matrix (The Haystack)
    groomed_matrix, groomed_ids = rebuild_groomed_cache(session, embedder)
    if len(groomed_ids) == 0:
        print("No groomed nodes exist. Falling back to Global Bootstrap or aborting.")
        pass_global_bootstrap(session, full_tree_text, dry_run=dry_run)
        return

    # Build groomed chain texts for BM25 corpus
    groomed_chain_texts = []
    for gid in groomed_ids:
        leaf = session.get(Topic, gid)
        groomed_chain_texts.append(build_chain_path_text(leaf, parent_map=parent_map))
    
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
    #    Pre-build leaf → root lookup (walk in-memory dict once per unique leaf)
    leaf_to_root = {}
    for gid in groomed_ids:
        if gid not in leaf_to_root:
            curr_id = gid
            parent_node = parent_map.get(curr_id)
            while parent_node is not None:
                curr_id = parent_node.id
                parent_node = parent_map.get(curr_id)
            leaf_to_root[gid] = curr_id

    #    Single argsort across all rows at once
    k = min(top_k, similarity_scores.shape[1])
    top_k_indices = np.argsort(similarity_scores, axis=1)[:, -k:][:, ::-1]

    #    Map indices → root IDs via the lookup, preserving order and filtering by similarity
    per_head_groomed_roots = []
    root_frequencies = {}
    groomed_branch_cache = {}
    groomed_branch_tokens = {}

    for i, row_indices in enumerate(top_k_indices):
        root_ids = []
        seen = set()
        for idx in row_indices:
            if similarity_scores[i, idx] < 0.4:
                continue
            rid = leaf_to_root.get(groomed_ids[idx])
            root_frequencies[rid] = root_frequencies.get(rid, 0) + 1
            if rid is not None and rid not in seen:
                g_root = session.get(Topic, rid)
                if rid not in groomed_branch_cache:
                    text = f"\n GROOMED BRANCH {g_root.id}\n" + build_branch_text(g_root, children_map=children_map)
                    groomed_branch_cache[rid] = text
                    groomed_branch_tokens[rid] = estimate_tokens(text)
                seen.add(rid)
                root_ids.append(rid)
        per_head_groomed_roots.append(root_ids)
    
    messy_chain_tokens = [estimate_tokens(t) for t in head_branch_texts]
    unprocessed_indices = set(range(len(messy_heads)))

    all_successfully_mapped_nodes = []

    # 8. Dynamic greedy token-based batching for LLM calls
    while unprocessed_indices:
        batch_messy_texts = []
        batch_groomed_root_ids = set()
        batch_nodes = []
        batch_token_count = 0
        batch_indices = []
        
        while unprocessed_indices:
            best_idx = None
            best_score = (float('inf'), 0)
            best_added_tokens = 0
            best_new_roots = set()
            
            for idx in unprocessed_indices:
                candidate_roots = per_head_groomed_roots[idx]
                new_roots = set(candidate_roots) - batch_groomed_root_ids
                new_root_tokens = sum(groomed_branch_tokens[r] for r in new_roots)
                
                head_tokens = messy_chain_tokens[idx]
                added_tokens = head_tokens + new_root_tokens
                
                # Capacity constraint check (always passes for the first item in an empty batch)
                if batch_messy_texts and (batch_token_count + added_tokens) > TOKEN_LIMIT:
                    continue
                    
                freq_bonus = sum(root_frequencies.get(r, 0) for r in candidate_roots)
                score = (new_root_tokens, -freq_bonus)
                
                if score < best_score:
                    best_score = score
                    best_idx = idx
                    best_added_tokens = added_tokens
                    best_new_roots = new_roots
            
            if best_idx is None:
                # No more remaining chains can fit in this specific batch
                break
                
            unprocessed_indices.remove(best_idx)
            batch_messy_texts.append(head_branch_texts[best_idx])
            batch_groomed_root_ids.update(best_new_roots)
            batch_nodes.extend(head_descendants[best_idx])
            batch_token_count += best_added_tokens
            batch_indices.append(best_idx)

        # Failsafe: if a single chain + its roots exceeds the TOKEN_LIMIT entirely on its own,
        # it will be indefinitely skipped by the capacity check above. We must force it through
        # in an isolated batch of 1 so it isn't dropped, letting the LLM handle the truncation.
        if not batch_indices:
            forced_idx = unprocessed_indices.pop()
            batch_messy_texts.append(head_branch_texts[forced_idx])
            batch_groomed_root_ids.update(set(per_head_groomed_roots[forced_idx]))
            batch_nodes.extend(head_descendants[forced_idx])
            batch_token_count += (messy_chain_tokens[forced_idx] + sum(groomed_branch_tokens[r] for r in per_head_groomed_roots[forced_idx]))

        batch_nodes = list(set(batch_nodes))
        
        # Include groomed nodes in target_nodes so the LLM can fix them too
        groomed_nodes_in_batch = []
        for rid in batch_groomed_root_ids:
            g_root = session.get(Topic, rid)
            groomed_nodes_in_batch.extend(_get_all_descendants(g_root, children_map=children_map))
        batch_nodes = list(set(batch_nodes + groomed_nodes_in_batch))
        
        groomed_context_text = "".join(groomed_branch_cache[rid] for rid in batch_groomed_root_ids)
        
        print(f"\n-> Grooming {len(batch_messy_texts)} messy chains (~{batch_token_count} tokens) against {len(batch_groomed_root_ids)} groomed branches...")
        
        prompt = f"""
        You are an expert knowledge architect. Integrate new messy branches into the existing groomed knowledge tree to optimize retrieval.
        
        CONTEXT: Branches marked [ESTABLISHED] are already organized. Branches marked [NEW MESSY] are new and need integration.
        You may ALSO fix issues in [ESTABLISHED] branches if you spot misplacements, scattered redundancy, or duplicates, but strictly obey the constraints below.
        
        HOW TO MAKE CHANGES:
        - To MOVE a node under a different parent: output {{"id": <existing_id>, "parent_id": <new_parent_id>}}
        - To MERGE duplicates: output {{"id": <duplicate_id>, "merged_into_id": <primary_id>}}
        - To RENAME: output {{"id": <existing_id>, "name": "New Name"}}
        - To make a node a root: output {{"id": <existing_id>, "parent_id": null}}
        
        HOW TO PROCESS NEW MESSY BRANCHES:
        1. Look at ONLY the leaf nodes (nodes with memories) in each messy branch.
        2. For each leaf, find the most semantically appropriate EXISTING groomed parent node by its ID.
        3. Re-parent the leaf DIRECTLY under that groomed parent.
        
        CRITICAL RULES & CONSTRAINTS:
        1. ELIMINATE REDUNDANCY: If a messy leaf duplicates an existing groomed node (same concept), set merged_into_id to the groomed node's ID. Redundancy destroys retrieval.
        2. IGNORE STRUCTURAL GENERAL NODES (CRITICAL): You will see leaf nodes named "General [Topic]" sitting under a parent named "[Topic]" (e.g. "General Business" under "Business"). 
           - DO NOT MERGE THESE! They are structurally required to hold memories for the parent node.
           - NEVER merge "General [Topic]" into "[Topic]".
        3. NO AGGRESSIVE ROOT SPLITTING: Do not arbitrarily burst existing hierarchies into dozens of tiny new roots. Nest topics naturally without aggressively flattening.
        4. RETRIEVAL-FRIENDLY HIERARCHY: Build a proper "knowledge tree". Example: Do NOT scatter "Italy", "Rome", "Europe Trip" as siblings. Nest them correctly (Europe -> Italy -> Rome) so the path makes logical sense.
        5. SIBLING ORTHOGONALITY (CRITICAL): Siblings must be mutually exclusive partitions, NOT semantic variations. 
           - Bad: "Personal Experience", "Personal Opinions", "Personal Preferences".
           - Good: "Subjective Feedback", "Objective Information".
           - MERGE siblings with semantic overlap into a single node. Ask: "If I remove this node, what queries become impossible to classify?" If another sibling would catch it, merge them.
        6. USE EXISTING GROOMED IDS: Always re-parent into existing groomed branch IDs.
        7. NO ORPHAN LEAVES: Every leaf must be placed under a meaningful parent.
        8. NO NAME REPETITION: A child must never share its name with its parent or any ancestor.
        9. SEMANTIC ACCURACY: Every node must be under a semantically correct parent.
        
        OUTPUT RULES:
        9. STRICT OUTPUT LIMIT: Only output nodes that NEED changes. Omit nodes that are already correct.
           - DO NOT RE-OUTPUT UNCHANGED NODES. If you return nodes that you did not change, you will crash the system due to token limits.
        10. Output leaf nodes (with memories) that need re-parenting or merging.
        11. Do NOT output parent_id as NEW_Root, NEW_General, NEW_Misc..
        
        EXISTING GROOMED BRANCHES [ESTABLISHED] (use these IDs as parent_id or merged_into_id targets):
        {groomed_context_text}
        
        NEW MESSY BRANCHES TO INTEGRATE (extract leaves and place them correctly):
        {"".join(batch_messy_texts)}
        """
        
        mapped_nodes = apply_mapping(session, ai, prompt, system_prompt, batch_nodes, children_map, node_map)
        if mapped_nodes:
            all_successfully_mapped_nodes.extend(mapped_nodes)

    return all_successfully_mapped_nodes


SPLIT_THRESHOLD = 25

def split_overloaded_leaves(session, changed_nodes = None, dry_run=True):
    """Find leaf nodes with 25+ memories and split them into sub-categories using the LLM."""
    ai = SafeAI()
    embedder = EmbeddingManager()
    
    # Find all leaf nodes (no children) with total memories >= threshold
    all_topics = session.query(Topic).all()
    overloaded = []
    for node in all_topics:
        if node.children:
            continue
        total = (len(node.episodic_memories) + len(node.user_memories) + 
                 len(node.knowledge_memories) + len(node.decision_memories))
        if total >= SPLIT_THRESHOLD:
            overloaded.append((node, total))
    
    if not overloaded:
        print("[Auto-Split] No overloaded leaf nodes found.")
        return
    
    print(f"\n[Auto-Split] Found {len(overloaded)} overloaded leaf nodes to split.")
    
    nodes_to_delete = []
    new_child_nodes = []
    
    for node, total in overloaded:
        # Build chain path for context
        chain_path = build_chain_path_text(node)
        
        # Collect all memories with their IDs and types
        memory_entries = []
        for mem in node.episodic_memories:
            memory_entries.append((mem.id, "episodic", mem.content))
        for mem in node.user_memories:
            memory_entries.append((mem.id, "user", mem.content))
        for mem in node.knowledge_memories:
            memory_entries.append((mem.id, "knowledge", mem.content))
        for mem in node.decision_memories:
            memory_entries.append((mem.id, "decision", mem.content))
        
        # Format memories for the prompt
        memory_text = "\n".join(
            f"  {mid} ({mtype}): {mcontent}" 
            for mid, mtype, mcontent in memory_entries
        )
        
        prompt = f"""You are an expert taxonomist. A leaf node in the knowledge tree has become overloaded with {total} memories and needs to be split into meaningful sub-categories.

            CURRENT NODE PATH: {chain_path}

            MEMORIES ({total} total):
            {memory_text}

            INSTRUCTIONS:
            1. Analyze the memories and identify 2-6 meaningful sub-categories that would organize them well.
            2. Each sub-category name should be specific and descriptive — NOT generic like "General" or "Other".
            3. Every memory ID must appear in exactly ONE sub-category.
            4. Sub-categories should be semantically coherent — group related memories together.
            5. If some memories don't fit any clear category, create a specific catch-all like "Miscellaneous [specific topic]".

            PARENT NODE DECISION:
            6. For each sub-category, set "placement" to decide where it goes:
               - "child": place as a child UNDER the current node (use when the current node name is meaningful and the sub-category belongs inside it).
               - "sibling": place at the SAME level as the current node, under its parent (use when the sub-category is independent or the current node is a generic wrapper like "General Travel").
            7. If ALL sub-categories are placed as "sibling", the current node will be auto-deleted if it has no remaining memories.

            Output valid JSON matching the schema."""
        
        system_prompt = "You are an expert taxonomist splitting an overloaded leaf node into meaningful sub-categories. Output valid JSON."
        
        raw_json = ai.generate(
            prompt=prompt, 
            system_prompt=system_prompt, 
            json_schema=LeafSplitSchema.model_json_schema()
        )
        if not raw_json:
            print(f"  [Auto-Split] Failed to get LLM response for '{node.name}' (ID {node.id})")
            continue
        
        try:
            data = json.loads(raw_json)
            sub_cats = data.get("sub_categories", [])
        except json.JSONDecodeError as e:
            print(f"  [Auto-Split] JSON Error for '{node.name}': {e}")
            continue
        
        if len(sub_cats) < 2:
            print(f"  [Auto-Split] LLM returned <2 sub-categories for '{node.name}', skipping.")
            continue
        
        if dry_run:
            print(f"  [Auto-Split DRY RUN] Would split '{node.name}' (ID {node.id}, {total} mems) into:")
            for sc in sub_cats:
                print(f"    - {sc['name']} ({len(sc['memory_ids'])} memories, {sc.get('placement', 'child')})")
            continue
        
        # Build memory ID → ORM object lookup
        mem_lookup = {}
        for mem in node.episodic_memories:
            mem_lookup[mem.id] = mem
        for mem in node.user_memories:
            mem_lookup[mem.id] = mem
        for mem in node.knowledge_memories:
            mem_lookup[mem.id] = mem
        for mem in node.decision_memories:
            mem_lookup[mem.id] = mem
        
        print(f"  [Auto-Split] Splitting '{node.name}' (ID {node.id}, {total} mems) into {len(sub_cats)} sub-categories:")
        
        for sc in sub_cats:
            child_name = sc["name"]
            child_mem_ids = sc.get("memory_ids", [])
            placement = sc.get("placement", "child")
            
            # Determine parent based on placement
            if placement == "sibling":
                parent = node.parent
                level = node.level
            else:
                parent = node
                level = node.level + 1
            
            child_node = Topic(
                name=child_name,
                level=level,
                parent=parent,
                is_groomed=1,
                chain_updated_at=datetime.now(),
                timestamp=datetime.now()
            )
            session.add(child_node)
            session.flush()
            
            # Reassign memories
            moved = 0
            for mid in child_mem_ids:
                mem_obj = mem_lookup.get(mid)
                if mem_obj:
                    mem_obj.topic = child_node
                    moved += 1
            
            new_child_nodes.append(child_node)
            print(f"    - '{child_name}' (ID {child_node.id}): {moved} memories, placed as {placement}")
        
        
        # Check if old node should be auto-deleted
        session.flush()
        session.expire(node)
        remaining = (len(node.episodic_memories) + len(node.user_memories) + 
                     len(node.knowledge_memories) + len(node.decision_memories))
        has_children = bool(node.children)
        
        if remaining == 0 and not has_children:
            nodes_to_delete.append(node)
            print(f"    Queued '{node.name}' (ID {node.id}) for deletion — no memories or children remain")
    
    # Batch cleanup: delete all empty old nodes
    for node in nodes_to_delete:
        session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == node.id).delete()
        session.delete(node)
    if nodes_to_delete:
        print(f"  Deleted {len(nodes_to_delete)} empty old nodes.")
    
    # Single embedder run for all new leaf nodes
    changed_nodes.extend(new_child_nodes)
    if changed_nodes:
        update_leaf_embedding_cache(session, changed_nodes, embedder)
        print(f"  Updated embeddings for {len(changed_nodes)} new leaf nodes.")
    
    session.commit()
    print("[Auto-Split] Complete.")

def cleanup_roots(session):
    """Post-processing: merge duplicate roots, dissolve generic roots, mark tiny orphans."""
    print("\n[Root Cleanup] Starting post-batch root cleanup...")
    
    roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
    
    # 1. Merge same-name roots (keep the one with most descendants)
    name_groups = defaultdict(list)
    for r in roots:
        name_groups[r.name.lower()].append(r)
    
    merge_count = 0
    for name, group in name_groups.items():
        if len(group) < 2:
            continue
        # Count total descendants for each root
        def count_descendants(node):
            total = 0
            stack = list(node.children)
            while stack:
                n = stack.pop()
                total += 1
                stack.extend(n.children)
            return total
        
        group.sort(key=count_descendants, reverse=True)
        survivor = group[0]
        
        for dup in group[1:]:
            print(f"  ROOT-MERGE: '{dup.name}' (ID {dup.id}, {count_descendants(dup)} descendants) INTO '{survivor.name}' (ID {survivor.id})")
            # Transfer children
            for child in list(dup.children):
                child.parent = survivor
            # Transfer memories
            for mem in list(dup.episodic_memories): mem.topic = survivor
            for mem in list(dup.user_memories): mem.topic = survivor
            for mem in list(dup.knowledge_memories): mem.topic = survivor
            for mem in list(dup.decision_memories): mem.topic = survivor
            # Delete cache and node
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == dup.id).delete()
            session.delete(dup)
            merge_count += 1
    
    if merge_count > 0:
        session.flush()
        print(f"  Merged {merge_count} duplicate roots.")
    
    # 2. Dissolve generic roots ("root", "general", "misc", "other")
    GENERIC_NAMES = {"root", "general", "misc", "other", "miscellaneous"}
    session.expire_all()
    roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
    dissolve_count = 0
    for r in roots:
        if r.name.lower().strip() in GENERIC_NAMES:
            print(f"  ROOT-DISSOLVE: '{r.name}' (ID {r.id}) — elevating {len(list(r.children))} children to roots")
            for child in list(r.children):
                child.parent = None
                child.is_groomed = 0
            # Transfer any memories to the first child or just let them go with the node
            for mem in list(r.episodic_memories): mem.topic = None
            for mem in list(r.user_memories): mem.topic = None
            for mem in list(r.knowledge_memories): mem.topic = None
            for mem in list(r.decision_memories): mem.topic = None
            session.query(TopicEmbeddingCache).filter(TopicEmbeddingCache.topic_leaf_id == r.id).delete()
            session.delete(r)
            dissolve_count += 1
    
    if dissolve_count > 0:
        session.flush()
        print(f"  Dissolved {dissolve_count} generic-named roots.")
    
    # 3. Mark tiny orphan roots (<=3 total nodes) as ungroomed for future pickup
    session.expire_all()
    roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
    tiny_count = 0
    for r in roots:
        total_nodes = 1 + sum(1 for _ in _get_all_descendants(r)) - 1  # -1 because _get_all_descendants includes self
        if total_nodes <= 3:
            r.is_groomed = 0
            for desc in _get_all_descendants(r):
                desc.is_groomed = 0
            tiny_count += 1
            print(f"  TINY-ROOT: '{r.name}' (ID {r.id}, {total_nodes} nodes) marked ungroomed for future placement")
    
    if tiny_count > 0:
        session.flush()
        print(f"  Marked {tiny_count} tiny roots as ungroomed.")
    
    session.commit()
    print("[Root Cleanup] Complete.")


def reorganize_tree(dry_run=True):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        all_roots = session.query(Topic).filter(Topic.parent_id.is_(None)).all()
        current_root_count = len(all_roots)
        last_root_count = _load_last_root_count()
        full_tree_text = ""
        for r in all_roots:
            full_tree_text += build_branch_text(r)
        total_tokens = estimate_tokens(full_tree_text)
        
        print(f"Total graph tokens estimated: {total_tokens}/{TOKEN_LIMIT}")
        # 0. Cache full DB into memory maps to avoid ORM N+1 performance death
        children_map = defaultdict(list)
        topic_by_id = {None: None}
        topics = session.query(Topic).all()
        for t in topics:
            topic_by_id[t.id] = t
            if t.parent_id is not None:
                children_map[t.parent_id].append(t)
        parent_map = {t.id: topic_by_id.get(t.parent_id) for t in topics}
        if last_root_count == current_root_count:
            print(f"[Root Orthogonality] Skipping Phase 0: root count unchanged at {current_root_count}.")
        else:
            pass_root_orthogonality(session, children_map=children_map, node_map=topic_by_id)
            _save_last_root_count(current_root_count)
        
        if total_tokens < TOKEN_LIMIT:
            nodes = pass_global_bootstrap(session, full_tree_text, children_map=children_map, node_map=topic_by_id)
        else:
            nodes = pass_targeted_vector(session, full_tree_text, top_k=3, parent_map=parent_map, children_map=children_map, node_map=topic_by_id)
        cleanup_roots(session)
        # Post-processing: split any overloaded leaf nodes
        split_overloaded_leaves(session, nodes, dry_run=dry_run)
            
    finally:
        session.close()
        print("\nAll grooming completed successfully.")

if __name__ == "__main__":
    dry_run = False
    reorganize_tree(dry_run=dry_run)
