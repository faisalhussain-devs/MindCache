import sys
import io
sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

import json
import os
import numpy as np
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from mindcache.Database.db_setup import Topic, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory, to_numpy, Session
from mindcache.Database.embedder import EmbeddingManager
from mindcache.Memory_extract.safe_ai import SafeAI
from pydantic import BaseModel, Field
from typing import List, Union, Optional
from collections import defaultdict
import re
import igraph as ig
import leidenalg
from sklearn.neighbors import NearestNeighbors
from concurrent.futures import ThreadPoolExecutor
import logging
logger = logging.getLogger(__name__)


def normalize_topic_name(name: str) -> str:
    """Normalize a topic name for fuzzy dedup comparison.
    Handles singular/plural (ies -> y, trailing s), lowercases, collapses whitespace."""
    if not name:
        return ""
    n = name.lower().strip()
    if n.startswith("general"):
        n = n[len("general "):]
    n = re.sub(r'\s+', ' ', n)   # collapse whitespace
    if n.endswith("ies"):
        n = n[:-3] + "y"
    elif n.endswith("s") and not n.endswith("ss"):
        n = n[:-1]
    return n


def _precompute_memory_counts(session, user_id="default"):
    """Executes 4 fast SQL queries to count memories group-by topic_id."""
    from sqlalchemy import func
    counts = defaultdict(int)
    for model in [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]:
        res = session.query(model.topic_id, func.count(model.id)).filter(model.user_id == user_id).group_by(model.topic_id).all()
        for topic_id, count in res:
            if topic_id is not None:
                counts[topic_id] += count
    return counts

ROOT_ORTHO_STATE_FILE = os.path.join(os.path.dirname(__file__), ".root_orthogonality_state.json")

class ReorganizedNode(BaseModel):
    id: Union[int, str] = Field(description="Original ID of the node.")
    name: Optional[str] = Field(default=None, description="OMIT THIS KEY if the name is not changing. Only include if renaming the category.")
    parent_id: Optional[Union[int, str]] = Field(default=None, description="OMIT THIS KEY if the parent is not changing. Only include if re-parenting. Use null to make the node a root, or a string ID starting with 'NEW_' (e.g. 'NEW_Category') to create a new category/parent node on the fly.")
    merged_into_id: Optional[int] = Field(default=None, description="OMIT THIS KEY if the node is not being merged. Only include if merging into another ID.")

class TreeReorganizationSchema(BaseModel):
    modified_nodes_only: List[ReorganizedNode] = Field(description="ONLY include nodes that require a change (e.g., merging, newly assigned parent, or renamed). Do NOT include nodes that are already correctly placed and need no changes.")

class SubCategory(BaseModel):
    name: str = Field(description="Name of the sub-category.")
    memory_ids: List[int] = Field(description="List of memory IDs that belong to this sub-category.")
    placement: str = Field(description="'child' if sub-categories should be children of the current node, or 'sibling' if they should replace the current node at the same level (as siblings to it under its parent).")

class LeafSplitSchema(BaseModel):
    sub_categories: List[SubCategory] = Field(description="List of sub-categories to split the overloaded leaf into. Each memory ID must appear in exactly one sub-category.")

class NodeGrouping(BaseModel):
    category_name: str = Field(description="Name of the new intermediate grouping category.")
    child_ids: List[int] = Field(description="List of original child node IDs that belong in this group.")

class GroupOvergrownChildrenSchema(BaseModel):
    groupings: List[NodeGrouping] = Field(description="List of new intermediate categories to group the overgrown children into. Every child ID must be grouped.")

TOKEN_LIMIT = 6000

# Root names considered generic/catch-all — used by cleanup and relocation passes.
GENERIC_NAMES = {"root", "general", "misc", "other", "miscellaneous"}
TINY_ROOT_THRESHOLD = 13  # roots with <= this many total descendants are candidates for relocation

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def build_branch_text(node, depth=0, lines=None, children_map=None, mem_count_map=None):
    if lines is None:
        lines = []

    if children_map is not None:
        children = children_map.get(node.id, [])
    else:
        children = node.children

    child_count = len(children)
    prefix = "\t" * depth
    if mem_count_map is not None:
        if child_count == 0:  # leaf node — show memory count
            mem_count = mem_count_map.get(node.id, 0)
            lines.append(f"{prefix}{node.id}: {node.name} [memories:{mem_count}]")
        else:  # parent node — show children count
            lines.append(f"{prefix}{node.id}: {node.name} [children:{child_count}]")
    else:
        lines.append(f"{prefix}{node.id}: {node.name} ({child_count})")

    for child in children:
        build_branch_text(child, depth + 1, lines, children_map, mem_count_map)
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

def _merge_summary_blob(target_summary, source_summary):
    """Merge summary blobs without breaking structured JSON summaries."""
    if not source_summary:
        return target_summary, False
    if not target_summary:
        return source_summary, True

    target_text = str(target_summary).strip()
    source_text = str(source_summary).strip()

    try:
        if target_text.startswith("{") and source_text.startswith("{"):
            target_obj = json.loads(target_text)
            source_obj = json.loads(source_text)
            if isinstance(target_obj, dict) and isinstance(source_obj, dict):
                merged_obj = json.loads(target_text)

                def merge_dict(dst, src):
                    changed = False
                    for key, value in src.items():
                        if key not in dst:
                            dst[key] = value
                            changed = True
                            continue
                        existing = dst[key]
                        if isinstance(existing, dict) and isinstance(value, dict):
                            if merge_dict(existing, value):
                                changed = True
                        elif isinstance(existing, list) and isinstance(value, list):
                            for item in value:
                                if item not in existing:
                                    existing.append(item)
                                    changed = True
                        elif existing in (None, "", [], {}):
                            dst[key] = value
                            changed = True
                    return changed

                changed = merge_dict(merged_obj, source_obj)
                if changed:
                    return json.dumps(merged_obj), True
    except Exception:
        pass

    if target_text != source_text and not target_text.startswith("{") and not source_text.startswith("{"):
        return f"{target_text}\n\n[Merged source]\n{source_text}", True

    return target_summary, False


def transfer_topic_metadata(source_node, target_node):
    """Transfer non-memory metadata from a deleted source node to its survivor."""
    summary_changed = False
    description_changed = False

    merged_summary, changed = _merge_summary_blob(target_node.summary, source_node.summary)
    if changed:
        target_node.summary = merged_summary
        summary_changed = True

    source_description = (source_node.description or "").strip()
    target_description = (target_node.description or "").strip()
    if source_description and not target_description:
        target_node.description = source_node.description
        description_changed = True
    elif source_description and target_description and source_description != target_description:
        if source_description not in target_description:
            target_node.description = f"{target_node.description}\n\n[Merged from node {source_node.id}]\n{source_node.description}"
            description_changed = True

    if source_node.timestamp and (target_node.timestamp is None or source_node.timestamp < target_node.timestamp):
        target_node.timestamp = source_node.timestamp

    if summary_changed or description_changed:
        target_node.embedding = None
    elif target_node.embedding is None and source_node.embedding is not None:
        target_node.embedding = source_node.embedding

    return summary_changed or description_changed


def enforce_leaf_constraint(session, user_id="default", successfully_mapped_nodes=None, counts=None):
    if counts is None:
        counts = _precompute_memory_counts(session, user_id=user_id)
    session.expire_all()
    leaf_enforcement_count = 0
    for node in list(session.query(Topic).filter(Topic.user_id == user_id).all()):
        has_memories = counts.get(node.id, 0) > 0
        
        # If it has children AND holds memories, we must split it
        if node.children and has_memories:
            original_name = node.name
            if not original_name.startswith("General "):
                # Create a NEW parent node to take its place in the hierarchy
                new_parent = Topic(
                    name=original_name,
                    level=node.level,
                    parent=node.parent,
                    timestamp=node.timestamp,
                    user_id=user_id
                )
                session.add(new_parent)
                logger.info(f"  LEAF-ENFORCEMENT: Split '{original_name}' (ID {node.id}). Created new parent.")
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
                        timestamp=node.timestamp,
                        user_id=user_id
                    )
                    session.add(parent)
                for child in list(node.children):
                    child.parent = parent
                node.parent = parent
                node.level = level + 1                
                # The current node is now a leaf and its name changed. Queue it for cache update.
            if successfully_mapped_nodes is not None and node not in successfully_mapped_nodes:
                successfully_mapped_nodes.append(node)       
            leaf_enforcement_count += 1

    if leaf_enforcement_count > 0:
        session.flush()
        logger.info(f"  Enforced leaf constraint on {leaf_enforcement_count} nodes (memories moved to generic children).")
    return leaf_enforcement_count


def apply_mapping(session, user_id="default", ai=None, prompt=None, system_prompt=None, target_node_ids=None, children_map=None, node_map=None, raw_json=None):
    # Rebuild maps if not provided, to ensure fresh and consistent state across batches
    if node_map is None:
        node_map = {t.id: t for t in session.query(Topic).filter(Topic.user_id == user_id).all()}
    if children_map is None:
        children_map = defaultdict(list)
        for t in node_map.values():
            children_map[t.parent_id].append(t)

    if raw_json is None:
        raw_json = ai.generate(prompt=prompt, system_prompt=system_prompt, json_schema=TreeReorganizationSchema.model_json_schema())
    if not raw_json:
        logger.info("Failed to get LLM response.")
        return [], False
    try:
        data = json.loads(raw_json)
        nodes_data = data.get("modified_nodes_only", [])
    except json.JSONDecodeError as e:
        logger.error(f"JSON Error: {e}\n")
        logger.info("Attempting to repair truncated JSON...")
        try:
            # Find the last properly closed JSON object "}"
            last_brace = raw_json.rfind('}')
            if last_brace != -1:
                # Truncate and close out the JSON array and root object
                repaired_json = raw_json[:last_brace+1] + "\n]}"
                data = json.loads(repaired_json)
                nodes_data = data.get("modified_nodes_only", [])
                logger.info(f"Successfully rescued {len(nodes_data)} node changes from truncated output.")
            else:
                logger.info("Could not find a valid object brace to repair.")
                return [], False
        except Exception as repair_e:
            logger.info(f"Repair failed: {repair_e}\n")
            return [], False
    any_changes = False
    auto_adjusted_node_ids = set()  # internal safety moves (not explicit LLM intent)
    
    # 1. Create NEW Parents (case-insensitive, stored as dict for lookup)
    new_parents_db = {}
    for n_data in nodes_data:
        nid = n_data.get("id")
        pid = n_data.get("parent_id")
        for val in (nid, pid):
            if isinstance(val, str) and val.lower().startswith("new_"):
                val_key = val.lower()
                if val_key not in new_parents_db:
                    raw_name = val[4:]  # Slice off "new_" / "NEW_" prefix
                    clean_name = raw_name.replace("_", " ").strip()
                    # If the name is camelCase/PascalCase without spaces, split it
                    if " " not in clean_name:
                        clean_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', clean_name)
                    # Normalize whitespace
                    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
                    # If fully lowercase or fully uppercase, convert to Title Case
                    if clean_name.islower() or clean_name.isupper():
                        clean_name = clean_name.title()
                    
                    new_topic = Topic(name=clean_name, level=0, user_id=user_id)
                    session.add(new_topic)
                    session.flush()
                    new_parents_db[val_key] = new_topic
                    node_map[new_topic.id] = new_topic
                    logger.info(f"  Created NEW category: {new_topic.name} (DB ID: {new_topic.id})")
                    any_changes = True
                
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
                logger.info(f"  Resolved cycle: lifted '{bridge.name}' (ID {bridge.id}) out from under '{db_node.name}' (ID {node_id})")
            else:
                logger.info(f"  SKIPPED merge {node_id} -> {merge_target_id} (cycle could not be resolved)")
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

        transfer_topic_metadata(db_node, target_node)

        children_list = list(db_node.children)
        logger.info(f"  MERGE: '{db_node.name}' (ID {db_node.id}, parent={db_node.parent_id}) INTO '{target_node.name}' (ID {target_node.id}, parent={target_node.parent_id})")
        logger.info(f"    Children to move: {[(c.name, c.id) for c in children_list]}")
        for child in children_list:
            old_pid = child.parent_id
            if not _would_create_cycle(node_map, child.id, target_node.id):
                child.parent = target_node
                logger.info(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} -> {target_node.id} (target)")
            else:
                # Fallback: inherit source's parent (grandparent) — always safe
                if db_node.parent is not None:
                    child.parent = db_node.parent
                else:
                    child.parent = target_node # very exceptional case creating a cycle
                auto_adjusted_node_ids.add(child.id)
                logger.info(f"    CHILD '{child.name}' (ID {child.id}): parent {old_pid} -> {db_node.parent_id} (grandparent fallback, cycle)")
            nodes_reparented_by_merge.add(child.id)
        
        # Clean up ALL in-memory maps before deleting
        if children_map and db_node.parent_id in children_map:
            children_map[db_node.parent_id] = [c for c in children_map[db_node.parent_id] if c.id != node_id]
        session.delete(db_node)
        merge_redirects[node_id] = merge_target_id
        del node_map[node_id]
        target_node.is_groomed = 1
        target_node.chain_updated_at = datetime.now()
        any_changes = True

    # 3. Update remaining targeted nodes (shifting + renaming + grooming)
    successfully_mapped_nodes = []
    # Add new topic IDs to target_ids so they are allowed to be processed in Step 3
    target_ids = set(target_node_ids) | {t.id for t in new_parents_db.values()}
    
    for n_data in nodes_data:
        raw_id = n_data["id"]
        db_node = None
        if isinstance(raw_id, str):
            db_node = new_parents_db.get(raw_id.lower())
        else:
            try:
                node_id = int(raw_id)
                db_node = node_map.get(node_id)
            except (TypeError, ValueError):
                continue
        if not db_node:
            continue
        
        # Rename if the LLM returned a new name
        if "name" in n_data and n_data["name"] is not None:
            db_node.name = n_data["name"]
            any_changes = True
        
        db_node.is_groomed = 1
        db_node.chain_updated_at = datetime.now()
        
        # Handle parent_id changes (step 3: shifting/moving nodes)
        if "parent_id" in n_data:
            # If merge already reparented this node, LLM's explicit parent_id overrides it
            is_override = db_node.id in nodes_reparented_by_merge
            if is_override:
                logger.info(f"  STEP3-OVERRIDE: '{db_node.name}' (ID {db_node.id}) — merge-reparented, but LLM wants explicit redirect")
            
            pid = n_data["parent_id"]
            old_pid = db_node.parent_id
            
            if isinstance(pid, str):
                pid_key = pid.lower()
                if pid_key in new_parents_db:
                    pid_int = new_parents_db[pid_key].id
                else:
                    logger.info(f"  STEP3-DANGLING: '{db_node.name}' (ID {db_node.id}): parent ref {pid} is not a created category")
                    continue
            elif pid is not None:
                try:
                    pid_int = int(pid)
                except (TypeError, ValueError):
                    continue
            else:
                pid_int = None
            
            if pid_int is not None:
                orig_pid = pid_int
                # Redirect if parent was merged into another node
                while pid_int in merge_redirects:
                    pid_int = merge_redirects[pid_int]
                if pid_int != orig_pid:
                    logger.info(f"  STEP3-REDIRECT: '{db_node.name}' (ID {db_node.id}): parent ref {orig_pid} -> {pid_int} (merge redirect)")
                if pid_int in node_map and not _would_create_cycle(node_map, db_node.id, pid_int):
                    db_node.parent = node_map[pid_int]
                    logger.info(f"  STEP3-MOVE: '{db_node.name}' (ID {db_node.id}): parent {old_pid} -> {pid_int}")
                    any_changes = True
                elif pid_int == db_node.id:
                    logger.info(f"  STEP3-BLOCKED: self-parent for '{db_node.name}' (ID {db_node.id})")
                elif pid_int not in node_map:
                    logger.info(f"  STEP3-DANGLING: '{db_node.name}' (ID {db_node.id}): parent ref {pid_int} not in node_map! Keeping parent={old_pid}")
            elif not is_override:
                # Only allow making root if NOT a merge-override (prevent accidental root elevation)
                db_node.parent = None
                logger.info(f"  STEP3-ROOT: '{db_node.name}' (ID {db_node.id}): parent {old_pid} -> NULL (elevated to root)")
                any_changes = True
            
        successfully_mapped_nodes.append(db_node)
    
    # Flush step 3 parent changes before orphan cleanup
    session.flush()

    # 4. Enforce Leaf Node Constraint for Memories
    leaf_splits = enforce_leaf_constraint(session, user_id=user_id, successfully_mapped_nodes=successfully_mapped_nodes)
    if leaf_splits > 0:
        any_changes = True

    # 5. Sibling Dedup: merge same-name children under the same parent BEFORE empty node cleanup
    session.expire_all()
    counts_sibling = _precompute_memory_counts(session, user_id=user_id)

    # Rebuild node_map and children_map to include all step 2 and step 3 parent-child moves
    node_map = {t.id: t for t in session.query(Topic).filter(Topic.user_id == user_id).all()}
    children_map = defaultdict(list)
    for t in node_map.values():
        children_map[t.parent_id].append(t)
        
    sibling_merges = 0
    for pid, children in list(children_map.items()):
        # Group by normalized (fuzzy) name — catches plural/singular like Logarithm vs Logarithms
        name_groups = defaultdict(list)
        for s in children:
            key = normalize_topic_name(s.name)
            name_groups[key].append(s)
        
        for _, group in name_groups.items():
            if len(group) < 2:
                continue
            # Sort by total memory count descending — keep the richest
            def mem_count(n):
                return counts_sibling.get(n.id, 0)
            group.sort(key=mem_count, reverse=True)
            survivor = group[0]
            
            # Ensure survivor's cache gets rebuilt
            if survivor not in successfully_mapped_nodes:
                successfully_mapped_nodes.append(survivor)
                
            for dup in group[1:]:
                logger.info(f"  SIBLING-DEDUP: '{dup.name}' (ID {dup.id}) INTO '{survivor.name}' (ID {survivor.id}) under parent={pid}")
                # Transfer children
                for child in list(dup.children):
                    child.parent = survivor
                # Transfer memories via relationship
                for mem in list(dup.episodic_memories): mem.topic = survivor
                for mem in list(dup.user_memories): mem.topic = survivor
                for mem in list(dup.knowledge_memories): mem.topic = survivor
                for mem in list(dup.decision_memories): mem.topic = survivor
                # Clean up ALL maps, and delete
                if children_map and pid in children_map:
                    children_map[pid] = [c for c in children_map[pid] if c.id != dup.id]
                if node_map and dup.id in node_map:
                    del node_map[dup.id]
                session.delete(dup)
                sibling_merges += 1
    if sibling_merges > 0:
        session.flush()
        logger.info(f"  Merged {sibling_merges} same-name sibling duplicates.")
        any_changes = True


    # 6. Clean up orphaned empty nodes (loop to cascade: deleting a leaf may make its parent empty)
    session.expire_all()  # Force SQLAlchemy to re-read relationships from DB
    counts_cleanup = _precompute_memory_counts(session)
    
    # Rebuild node_map and children_map to include leaf enforcement changes
    node_map = {t.id: t for t in session.query(Topic).filter(Topic.user_id == user_id).all()}
    children_map = defaultdict(list)
    for t in node_map.values():
        children_map[t.parent_id].append(t)
        
    total_deleted = 0
    while True:
        count_deleted = 0
        for node in list(node_map.values()):
            has_memories = counts_cleanup.get(node.id, 0) > 0
            if not has_memories and not children_map.get(node.id):
                logger.info(f"  CLEANUP-DELETE: '{node.name}' (ID {node.id}, parent={node.parent_id}) — empty, no children")
                # Clean up ALL in-memory maps
                if node.parent_id in children_map:
                    children_map[node.parent_id] = [c for c in children_map[node.parent_id] if c.id != node.id]
                if node.id in node_map:
                    del node_map[node.id]
                session.delete(node)
                count_deleted += 1
        if count_deleted == 0:
            break
        total_deleted += count_deleted
    if total_deleted > 0:
        session.flush()
        logger.info(f"  Deleted {total_deleted} empty orphaned/duplicate nodes.")
        any_changes = True
    
    # 7. Mark ALL surviving target nodes as groomed (LLM omits nodes already okay)
    for tgt_id in target_node_ids:
        if tgt_id in node_map:
            db_tgt = node_map[tgt_id]
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
    # Rebuild maps once to ensure absolute correctness of parent/child levels
    node_map = {t.id: t for t in session.query(Topic).filter(Topic.user_id == user_id).all()}
    children_map = defaultdict(list)
    for t in node_map.values():
        children_map[t.parent_id].append(t)
        
    roots = [t for t in node_map.values() if t.parent_id is None]
    queue = [(r, 0) for r in roots]
    visited = set()
    while queue:
        node, level = queue.pop(0)
        if node.id in visited:
            continue
        visited.add(node.id)
        node.level = level
        for child in children_map.get(node.id, []):
            queue.append((child, level + 1))

    session.commit()
    session.expire_all()
    logger.info("  Mapping applied and committed.")
    return successfully_mapped_nodes, any_changes

def pass_global_bootstrap(session, user_id="default", full_tree_text="", children_map=None, node_map=None):
    logger.info(" PHASE 1: GLOBAL BOOTSTRAP (SMALL DB)")
    ai = SafeAI()

    system_prompt = "You are an expert knowledge architect building a stable, scalable ontology for an AI memory retrieval system. Optimize for semantic clarity, concept separation, and future growth — NOT for minimum node count. Output valid JSON."

    prompt = f"""\
You are an expert knowledge architect. The entire knowledge tree is shown below (small DB — full global view available). \
Restructure it into a clean, multi-rooted knowledge tree optimized for semantic retrieval.

PRIMARY OBJECTIVE:
Optimize for semantic clarity and future scalability, NOT minimum node count.
A larger tree with clear concept boundaries is preferable to a smaller tree with broad catch-all categories.
Merge only TRUE semantic duplicates — concepts that are merely related should remain separate as siblings under a shared parent.

NODE FORMAT: Each line is: <id>: <name> [children:N] for parent nodes, or <id>: <name> [memories:N] for leaf nodes.
- A leaf with [memories:0] is an EMPTY orphan — safe to delete (merged_into or simply flag for cleanup).
- A leaf with high [memories:N] holds important data — never discard it carelessly during a merge.
- Do NOT merge a high-memory leaf into another node without being certain they are true semantic duplicates.

HOW TO MAKE CHANGES:
- RENAME:          {{"id": <id>, "name": "New Name"}}
- MOVE:            {{"id": <id>, "parent_id": <new_parent_id>}}
- MERGE duplicate: {{"id": <dup_id>, "merged_into_id": <primary_id>}}
  NOTE: Merges auto-reparent all children of the deleted node. If a child does NOT belong under the merge target, add a SEPARATE parent_id change for it.
- ELEVATE TO ROOT: {{"id": <id>, "parent_id": null}}
  Use this ONLY to break distinct major domains (e.g. "Mathematics", "Health") out from a broad wrapper root or to consolidate fragmented same-domain roots.
- CREATE NEW CATEGORY: To group nodes under a new parent category, set their "parent_id" to a string starting with "NEW_" (e.g. "NEW_CategoryName"). You can also specify parent_id for the new category itself (e.g., set it to null for a new root category, or an integer/string ID to place it under a parent).

MANDATORY FIXES (in priority order):

A. CORRECT ONTOLOGY AND PARENT-CHILD RELATIONSHIPS: Ensure every node is under the most semantically correct parent.
   - Organize by WHAT a concept IS, not why it came up in conversation.
   - If a parent has many unrelated children, create intermediate categories to group related siblings.

B. MERGE SEMANTIC DUPLICATES — INCLUDING ACROSS DIFFERENT BRANCHES:
   Actively scan ALL subtrees for the SAME concept appearing under different parent branches.
   Cross-compare ALL sibling groups. If two nodes represent the same concept but live under
   different parents, merge them into the semantically correct canonical location.
   Examples of cross-branch duplicates that MUST be merged:
     "Savings Goal > Down Payment" AND "Savings Strategy > Down Payment Savings" → merge
     "Property Assessment > Home Inspection" AND "Property Assessment > Inspections" → merge
     "Budgeting > Apartment X" AND "Specific Property Instances > Apartment X" → merge
   Do NOT merge concepts merely because they are strongly related:
     Do NOT Merge: "Sleep Tracking" and "Sleep Quality" (related but distinct)
     Do NOT Merge: "Meditation" and "Relaxation Techniques" (meditation is a type of relaxation)
     Do NOT Merge: "CBT-I" and "Sleep Hygiene" (different treatment approaches)

C. CONSOLIDATE FRAGMENTED ROOTS: If you see many roots that belong to the same broad domain, \
merge them under a single authoritative root using MOVE operations. Leave only truly orthogonal domains as separate roots.

D. CREATE MEANINGFUL INTERMEDIATE CATEGORIES: If a parent has many distinct but related children, \
create intermediate categories rather than leaving a flat, overly wide node.
   Bad:  Sleep Quality > [Meditation, Napping, Exercise, Bedtime Routine, Screen Time, ...]
   Good: Sleep Quality > Relaxation Techniques > Meditation
                        > Schedule Management > [Bedtime Routine, Napping]
                        > Lifestyle Factors > Exercise

E. FLATTEN POINTLESS WRAPPERS: If a parent has only one child and adds no semantic meaning beyond the child, remove the wrapper.
   Flatten: "Child Development > Adolescent Development > General Adolescent Development"
   Do NOT flatten meaningful ontology layers: "Sleep Technology > Sleep Tracking" should stay even with one child.

F. ENFORCE MAXIMUM CHILD WIDTH:
   No node should have more than 7 direct children.
   If any node currently has more than 7 direct children, you MUST create 2–4 intermediate
   grouping categories to group related children under them.
   Count the [children:N] value shown for each parent node. Any parent with [children:N] where N > 7
   requires mandatory restructuring.
   Failure to enforce this is a critical structural error.

GENERAL NODE RULE:
Nodes named "General [Topic]" are automatically generated memory overflow buckets.
Do NOT merge, remove, rename, or reorganize them. Evaluate the surrounding ontology instead.

ANTI-DUMPING RULE:
Do NOT create or expand broad catch-all categories. Avoid categories like:
  "Improvement Strategies", "General Improvement Strategies", "Resources", "Benefits",
  "Methods", "Techniques", "Information", "Miscellaneous"
unless ALL children are genuinely instances of the same specific concept.
Do NOT collapse many distinct concepts into a generic parent simply because they are related.

FUTURE GROWTH RULE:
Prefer categories that will remain semantically coherent as new memories accumulate.
Avoid creating parents that would naturally attract many unrelated future memories.
A category should still make sense after it grows to 100x its current size.
  Good category: "Sleep Tracking" (clear, bounded concept)
  Bad category: "Improvement Strategies" (attracts everything)

ROOT QUALITY RULES:
Root categories should represent major, long-lived domains.
Avoid roots that: contain very few memories, have only one meaningful child, or represent a relationship rather than a concept.
Examples of weak roots that should be merged under broader domains: "Resources", "Benefits", "Travel" (with very few memories).

NAMING RULE:
Do NOT spend output tokens on capitalization fixes, singular/plural differences, spacing differences, or minor naming variants. These are automatically normalized by the system. Focus on semantic structure only.

STRUCTURAL RULES:
1. ONTOLOGICAL STRUCTURE: Organize by WHAT a concept IS, not why it came up.
2. SIBLING ORTHOGONALITY: Siblings must be mutually exclusive. Merge overlapping siblings.
3. DOMAIN-FIRST HIERARCHY: Domain > Sub-domain > Specific Concept.
4. NO NAME REPETITION: A child must never share its name with any ancestor. Flatten if found.

OUTPUT RULES:
- ONLY output nodes that NEED changes. Omit already-correct nodes.
- Do NOT re-output the entire tree — only the changed nodes.

ALL NODES:
{full_tree_text}
"""
    # Issue 5 fix: derive target_nodes from node_map for guaranteed consistency
    target_node_ids = [v.id for v in node_map.values() if v is not None]
    return apply_mapping(session, user_id, ai, prompt, system_prompt, target_node_ids, children_map=children_map, node_map=node_map)

REORG_PROMPT_TEMPLATE = """\
You are an expert knowledge architect. A subset of the knowledge tree is shown below.
{context_note}

PRIMARY OBJECTIVE:
Optimize for semantic clarity and future scalability, NOT minimum node count.
A larger tree with clear concept boundaries is preferable to a smaller tree with broad catch-all categories.
Merge only TRUE semantic duplicates — concepts that are merely related should remain separate as siblings under a shared parent.

NODE FORMAT: Each line is: <id>: <name> [children:N] for parent nodes, or <id>: <name> [memories:N] for leaf nodes.
- A leaf with [memories:0] is an EMPTY orphan — safe to delete (merged_into or flag for cleanup).
- A leaf with high [memories:N] holds important data — never discard it carelessly during a merge.
- Do NOT merge a high-memory leaf into another node without being certain they are true semantic duplicates.

HOW TO MAKE CHANGES:
- RENAME:          {{"id": <id>, "name": "New Name"}}
- MOVE:            {{"id": <id>, "parent_id": <new_parent_id>}}
- MERGE duplicate: {{"id": <dup_id>, "merged_into_id": <primary_id>}}
  NOTE: Merges auto-reparent all children of the deleted node. If a child does NOT belong under the merge target, add a SEPARATE parent_id change for it.
- CREATE NEW CATEGORY: To group nodes under a new parent category, set their "parent_id" to a string starting with "NEW_" (e.g. "NEW_CategoryName"). You can also specify parent_id for the new category itself.

MANDATORY FIXES (in priority order):

A. CORRECT ONTOLOGY AND PARENT-CHILD RELATIONSHIPS: Ensure every node is under the most semantically correct parent.
   If a parent has many unrelated children, create intermediate categories to group related siblings.

B. MERGE SEMANTIC DUPLICATES — INCLUDING ACROSS DIFFERENT BRANCHES:
   Actively scan ALL subtrees for the SAME concept appearing under different parent branches.
   Cross-compare ALL sibling groups. If two nodes represent the same concept but live under
   different parents, merge them into the semantically correct canonical location.
   Examples of cross-branch duplicates that MUST be merged:
     "Savings Goal > Down Payment" AND "Savings Strategy > Down Payment Savings" → merge
     "Property Assessment > Home Inspection" AND "Property Assessment > Inspections" → merge
     "Budgeting > Apartment X" AND "Specific Property Instances > Apartment X" → merge
   Do NOT merge concepts merely because they are strongly related:
     Do NOT Merge: "Sleep Tracking" and "Sleep Quality" (related but distinct)
     Do NOT Merge: "Meditation" and "Relaxation Techniques" (meditation is a type of relaxation)

C. CONSOLIDATE FRAGMENTED DOMAINS: If the same concept appears scattered under different parent branches, \
MERGE them into a single canonical location under the most appropriate parent.

D. CREATE MEANINGFUL INTERMEDIATE CATEGORIES: If a parent has many distinct but related children, \
create intermediate categories rather than leaving a flat, overly wide node or merging distinct concepts.
   Bad:  Sleep Quality > [Meditation, Napping, Exercise, Bedtime Routine, Screen Time, ...]
   Good: Sleep Quality > Relaxation Techniques > Meditation
                        > Schedule Management > [Bedtime Routine, Napping]
                        > Lifestyle Factors > Exercise

E. FLATTEN POINTLESS WRAPPERS: If a parent has only one child and adds no semantic meaning beyond the child, remove the wrapper.
   Flatten: "Child Development > Adolescent Development > General Adolescent Development"
   Do NOT flatten meaningful ontology layers: "Sleep Technology > Sleep Tracking" should stay even with one child.

F. ENFORCE MAXIMUM CHILD WIDTH:
   No node should have more than 7 direct children.
   If any node currently has more than 7 direct children, you MUST create 2–4 intermediate
   grouping categories to group related children under them.
   Count the [children:N] value shown for each parent node. Any parent with [children:N] where N > 7
   requires mandatory restructuring.
   Failure to enforce this is a critical structural error.

GENERAL NODE RULE:
Nodes named "General [Topic]" are automatically generated memory overflow buckets.
Do NOT merge, remove, rename, or reorganize them. Evaluate the surrounding ontology instead.

ANTI-DUMPING RULE:
Do NOT create or expand broad catch-all categories. Avoid categories like:
  "Improvement Strategies", "General Improvement Strategies", "Resources", "Benefits",
  "Methods", "Techniques", "Information", "Miscellaneous"
unless ALL children are genuinely instances of the same specific concept.
Do NOT collapse many distinct concepts into a generic parent simply because they are related.

FUTURE GROWTH RULE:
Prefer categories that will remain semantically coherent as new memories accumulate.
Avoid creating parents that would naturally attract many unrelated future memories.
A category should still make sense after it grows to 100x its current size.
  Good category: "Sleep Tracking" (clear, bounded concept)
  Bad category: "Improvement Strategies" (attracts everything)

NAMING RULE:
Do NOT spend output tokens on capitalization fixes, singular/plural differences, spacing differences, or minor naming variants. These are automatically normalized by the system. Focus on semantic structure only.

STRUCTURAL RULES:
1. ONTOLOGICAL STRUCTURE: Organize by WHAT a concept IS, not why it came up.
2. SIBLING ORTHOGONALITY: Siblings must be mutually exclusive. Merge overlapping siblings.
3. DOMAIN-FIRST HIERARCHY: Domain > Sub-domain > Specific Concept.
4. NO NAME REPETITION: A child must never share its name with any ancestor. Flatten if found.
5. DO NOT ELEVATE TO ROOT: This is a local dedup pass — do not use parent_id: null.

OUTPUT RULES:
- ONLY output nodes that NEED changes. Omit already-correct nodes.
- Do NOT re-output the entire tree — only the changed nodes.

NODES IN THIS BATCH:
{tree_text}
"""

def hnsw_leiden_cluster(embeddings, k_neighbors=15, target_size=400, max_cluster_size=500):
    """
    Graph-based clustering via k-NN graph + Leiden community detection.
    Returns (labels, centroids) with the same interface as kmeans_cosine().
    Adaptively sub-clusters any community exceeding max_cluster_size.
    Uses scikit-learn for k-NN graph construction.
    """
    N, D = embeddings.shape
    if N <= k_neighbors:
        k_neighbors = N - 1
        
    if k_neighbors < 1:
        # Edge case: only 1 leaf
        return np.zeros(N, dtype=int), embeddings.copy()

    # Build k-NN graph
    nn = NearestNeighbors(n_neighbors=k_neighbors + 1, metric='cosine')
    nn.fit(embeddings)
    distances, indices = nn.kneighbors(embeddings)
    
    edges = []
    weights = []
    
    for i in range(N):
        for j_idx in range(1, k_neighbors + 1):  # Skip self (index 0)
            neighbor = indices[i, j_idx]
            dist = distances[i, j_idx]
            similarity = max(0.0, 1.0 - dist)
            edges.append((i, neighbor))
            weights.append(similarity)
            
    # Create igraph
    G = ig.Graph(n=N, edges=edges, directed=False)
    G.es['weight'] = weights
    
    # Binary search for optimal resolution:
    # Goal: max cluster size stays close to target_size and under max_cluster_size.
    # CPMVertexPartition works well with resolution in [0.0001, 0.5].
    # Higher resolution → more, smaller clusters.
    # Lower resolution → fewer, larger clusters.
    low, high = 0.0001, 0.5
    best_labels = None
    best_score = float('inf')   # we want max_size as close to target_size as possible

    for _ in range(8):
        res = (low + high) / 2
        partition = leidenalg.find_partition(G, leidenalg.CPMVertexPartition, resolution_parameter=res, weights='weight')
        labels = np.array(partition.membership)

        unique, counts = np.unique(labels, return_counts=True)
        max_size = counts.max() if len(counts) > 0 else 0

        # Track the result closest to target_size (without exceeding max_cluster_size)
        if max_size <= max_cluster_size:
            score = abs(max_size - target_size) + 0.5 * np.std(counts)
            if score < best_score:
                best_labels = labels.copy()
                best_score = score
            if abs(max_size - target_size) < target_size * 0.10:
                break

        if max_size > max_cluster_size:
            # Clusters too big → need higher resolution to split them
            low = res
        elif max_size < target_size // 2:
            # Clusters too small → need lower resolution to merge them
            high = res
        else:
            # In acceptable range — keep narrowing toward target_size
            if max_size > target_size:
                low = res
            else:
                high = res

    if best_labels is None:
        best_labels = labels   # fallback: use last result
            
    # Sub-cluster any that still exceed max_cluster_size (fallback)
    labels = best_labels.copy()
    unique, counts = np.unique(labels, return_counts=True)
    next_label = labels.max() + 1
    
    for cluster_id, count in zip(unique, counts):
        if count > max_cluster_size:
            idx = np.where(labels == cluster_id)[0]
            sub_embeddings = embeddings[idx]
            sub_labels, _ = hnsw_leiden_cluster(sub_embeddings, k_neighbors=min(15, count-1), target_size=target_size, max_cluster_size=max_cluster_size)
            
            for sub_l in np.unique(sub_labels):
                if sub_l == 0:
                    labels[idx[sub_labels == sub_l]] = cluster_id
                else:
                    labels[idx[sub_labels == sub_l]] = next_label
                    next_label += 1
                    
    # Re-map labels to contiguous integers starting from 0
    unique_labels = np.unique(labels)
    label_map = {old: new for new, old in enumerate(unique_labels)}
    final_labels = np.array([label_map[l] for l in labels])
    
    K = len(unique_labels)
    centroids = np.zeros((K, D))
    for i in range(K):
        members = embeddings[final_labels == i]
        if len(members) > 0:
            mean = np.mean(members, axis=0)
            norm = np.linalg.norm(mean)
            centroids[i] = mean / (norm if norm > 1e-10 else 1.0)
            
    return final_labels, centroids

def _build_cluster_tree_text(cluster_ids, leaves, labels, node_map, children_map, mem_count_map):
    """Build the tree text for a set of Leiden cluster IDs. Shared by clustering passes."""
    c_leaves = [leaf for i, leaf in enumerate(leaves) if labels[i] in cluster_ids]
    c_nodes = set()
    for leaf in c_leaves:
        curr = leaf
        while curr:
            c_nodes.add(curr)
            curr = node_map.get(curr.parent_id)
    c_nodes = sorted(c_nodes, key=lambda x: (x.level, x.id))
    c_children_map = defaultdict(list)
    c_node_ids = {n.id for n in c_nodes}
    for n in c_nodes:
        if n.parent_id in c_node_ids:
            c_children_map[n.parent_id].append(n)
    c_roots = [n for n in c_nodes if n.parent_id not in c_node_ids]
    text = ""
    for r in c_roots:
        text += build_branch_text(r, children_map=c_children_map, mem_count_map=mem_count_map) + "\n\n"
    return text

def run_batch_llm(batch_idx, batch_c, leaves, labels, node_map, children_map, mem_count_map, ai, system_prompt):
    batch_cluster_set = set(batch_c)
    batch_leaves = [leaf for i, leaf in enumerate(leaves) if labels[i] in batch_cluster_set]
    batch_nodes = set()
    for leaf in batch_leaves:
        curr = leaf
        while curr:
            batch_nodes.add(curr)
            curr = node_map.get(curr.parent_id)
    batch_nodes = sorted(list(batch_nodes), key=lambda x: (x.level, x.id))

    batch_children_map = defaultdict(list)
    batch_node_ids = {n.id for n in batch_nodes}
    for n in batch_nodes:
        if n.parent_id in batch_node_ids:
            batch_children_map[n.parent_id].append(n)

    batch_roots = [n for n in batch_nodes if n.parent_id not in batch_node_ids]
    tree_text = ""
    for r in batch_roots:
        tree_text += build_branch_text(r, children_map=batch_children_map, mem_count_map=mem_count_map) + "\n\n"

    prompt = REORG_PROMPT_TEMPLATE.format(tree_text=tree_text, context_note="This is a subset of the tree (a clustered batch of highly related concepts). Focus strictly on deduplication and merging within this subset.")
    raw_json = ai.generate(prompt=prompt, system_prompt=system_prompt, json_schema=TreeReorganizationSchema.model_json_schema())
    batch_node_ids = [n.id for n in batch_nodes]
    return batch_idx, raw_json, batch_node_ids


def run_relocation_batch_llm(batch_idx, batch_c, healthy_leaves, labels, node_map, children_map, mem_count_map, problem_root_assignments, ai, system_prompt):
    batch_healthy_leaves = [leaf for i, leaf in enumerate(healthy_leaves) if labels[i] in batch_c]
    batch_nodes = set()
    for leaf in batch_healthy_leaves:
        curr = leaf
        while curr:
            batch_nodes.add(curr)
            curr = node_map.get(curr.parent_id)
    batch_nodes = sorted(list(batch_nodes), key=lambda x: (x.level, x.id))

    batch_children_map = defaultdict(list)
    batch_node_ids = {n.id for n in batch_nodes}
    for n in batch_nodes:
        if n.parent_id in batch_node_ids:
            batch_children_map[n.parent_id].append(n)
    batch_roots = [n for n in batch_nodes if n.parent_id not in batch_node_ids]

    tree_text = ""
    for r in batch_roots:
        tree_text += build_branch_text(r, children_map=batch_children_map, mem_count_map=mem_count_map) + "\n\n"

    assigned_problem_roots = []
    for c in batch_c:
        assigned_problem_roots.extend(problem_root_assignments.get(c, []))

    fresh_problem_roots = [node_map.get(r.id) for r in assigned_problem_roots if node_map.get(r.id)]

    # Helper to build problem tree text
    problem_tree_text = ""
    for r in fresh_problem_roots:
        problem_tree_text += build_branch_text(r, children_map=children_map, mem_count_map=mem_count_map) + "\n\n"

    context_note = f"""HIGHEST PRIORITY TASK: PROBLEM ROOT RELOCATION
You MUST resolve the problem roots listed below by finding them a proper semantic home in the main ontology tree above.
The previous tasks are secondary. Your main goal is moving, renaming, or merging the problem roots.
Evaluate each problem root:
  1. Should any of its children MOVE under an existing category shown above?
  2. Should the root itself be RENAMED to something meaningful and remain a root?
  3. Should the root MERGE into an existing category shown above?
Do NOT keep a generic root name. If it must remain a root, give it a specific name.
Do NOT elevate children to root level. Find them a proper parent.

PROBLEM ROOTS:
{problem_tree_text}"""

    prompt = REORG_PROMPT_TEMPLATE.format(tree_text=tree_text, context_note=context_note)

    for r in fresh_problem_roots:
        for desc in _get_all_descendants(r, children_map):
            if desc not in batch_nodes:
                batch_nodes.append(desc)

    raw_json = ai.generate(prompt=prompt, system_prompt=system_prompt, json_schema=TreeReorganizationSchema.model_json_schema())
    batch_node_ids = [n.id for n in batch_nodes]
    return batch_idx, raw_json, batch_node_ids


def kmeans_dedup_pass(session, user_id="default", children_map=None, node_map=None, mem_count_map=None):
    logger.info("\n PHASE 2: FLAT K-MEANS DEDUP (LARGE DB)")
    ai = SafeAI()
    embedder = EmbeddingManager()
    from mindcache.Database.db_manager import DatabaseManager

    # 1. Gather all leaves
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    leaves = [t for t in topics if len(children_map.get(t.id, [])) == 0]

    if len(leaves) < 2:
        logger.info("Not enough leaves for K-Means.")
        return

    # Build parent_map for _build_leaf_embed_text
    parent_map = {t.id: node_map.get(t.parent_id) for t in topics}

    # 2. Build embeddings for leaves using Path + newest memories (up to 8000 Nomic tokens).
    #    Reuse Topic.embedding cache where available — only re-embed leaves with no cached vector.
    to_embed = []
    leaf_vecs_map = {}  # leaf.id -> np.array

    for leaf in leaves:
        if leaf.embedding is not None:
            leaf_vecs_map[leaf.id] = DatabaseManager._from_blob(leaf.embedding)
        else:
            embed_text = DatabaseManager._build_leaf_embed_text(leaf, parent_map)
            to_embed.append((leaf, embed_text))

    cached_count = len(leaf_vecs_map)
    new_count = len(to_embed)
    logger.info(f"  Leaf embeddings: {cached_count} cached, {new_count} to embed...")

    if to_embed:
        texts = [b[1] for b in to_embed]
        vecs = embedder.get_batch_embeddings(texts)
        for j, (leaf, _) in enumerate(to_embed):
            leaf.embedding = embedder._to_blob(vecs[j])
            leaf_vecs_map[leaf.id] = vecs[j]
        session.commit()

    # Align leaf_vecs with leaves list order (required for labels alignment after kmeans)
    leaf_vecs = [leaf_vecs_map[leaf.id] for leaf in leaves]
    logger.info(f"  Leaf embeddings ready ({len(leaf_vecs)} total).")
    
    # 3. Cluster using HNSW + Leiden algorithm
    logger.info(f"  Clustering {len(leaves)} leaves using HNSW+Leiden (max_cluster_size=500)...")
    labels, centroids = hnsw_leiden_cluster(np.vstack(leaf_vecs), target_size=300, max_cluster_size=500)
    K = len(np.unique(labels))
    logger.info(f"  Leiden determined K={K} clusters.")
    similarity_matrix = np.dot(centroids, centroids.T)


    # 4. Token-budget-aware proximity batching
    # Target: fill each LLM batch to ~5000 tokens (TOKEN_LIMIT) from closest clusters first.
    # This is fluid — a dense cluster may fill a batch by itself; sparse ones get packed together.
    BATCH_TOKEN_BUDGET = TOKEN_LIMIT  # 5000 tokens (defined at top of file)

    # Estimate token cost of each individual cluster (uses module-level _build_cluster_tree_text)
    cluster_token_cost = {}
    for c in range(K):
        txt = _build_cluster_tree_text({c}, leaves, labels, node_map, children_map, mem_count_map)
        cluster_token_cost[c] = estimate_tokens(txt)
    logger.info("cluster_token_cost:", cluster_token_cost)

    unassigned = set(range(K))
    batches = []  # list of list[cluster_id]

    while unassigned:
        un_list = list(unassigned)
        
        # 1. Start the batch with the absolute strongest available pair
        if len(un_list) == 1:
            batches.append([un_list[0]])
            break
            
        # Extract the submatrix of similarity_matrix for the unassigned clusters
        sub_matrix = similarity_matrix[np.ix_(un_list, un_list)]
        # Mask the diagonal elements to avoid pairing a cluster with itself
        np.fill_diagonal(sub_matrix, -np.inf)
        
        # Find index of the absolute strongest available pair
        idx = np.argmax(sub_matrix)
        r, c = np.unravel_index(idx, sub_matrix.shape)
        c1, c2 = un_list[r], un_list[c]
        
        # We start the batch with the larger of the two (packs big items first)
        if cluster_token_cost[c1] > cluster_token_cost[c2]:
            base_c = c1
        else:
            base_c = c2
            
        unassigned.remove(base_c)
        batch_clusters = [base_c]
        batch_tokens = cluster_token_cost[base_c]
        
        # 2. Greedily pull in the next closest available cluster to the batch
        while unassigned:
            un_list_remaining = list(unassigned)
            # Average Linkage: compute average similarity to the batch for each unassigned cluster
            sims_to_batch = np.mean(similarity_matrix[np.ix_(un_list_remaining, batch_clusters)], axis=1)
            # Find the unassigned cluster with the highest similarity to the batch
            best_idx = np.argmax(sims_to_batch)
            best_u = un_list_remaining[best_idx]
            
            cost = cluster_token_cost[best_u]
            
            # 3. Budget Stop: If the closest cluster doesn't fit, break immediately.
            # Do NOT skip it to look for a smaller unrelated cluster.
            if batch_tokens + cost <= BATCH_TOKEN_BUDGET:
                batch_clusters.append(best_u)
                batch_tokens += cost
                unassigned.remove(best_u)
            else:
                break
                
        batches.append(batch_clusters)

    logger.info(f"  Formed {len(batches)} token-aware batches (target ~{BATCH_TOKEN_BUDGET} tokens each).")

    # 5. Process each batch
    system_prompt = "You are an expert knowledge architect building a stable, scalable ontology for an AI memory retrieval system. Optimize for semantic clarity, concept separation, and future growth — NOT for minimum node count. Output valid JSON."

    futures = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for idx, batch_c in enumerate(batches):
            logger.info(f"    - Submitting LLM Batch {idx+1}/{len(batches)} (Clusters: {batch_c})")
            futures.append(executor.submit(
                run_batch_llm, idx, batch_c, leaves, labels, node_map, children_map, mem_count_map, ai, system_prompt
            ))

    any_changes = False
    for f in futures:
        idx, raw_json, batch_node_ids = f.result()
        logger.info(f"\n  Applying LLM Batch {idx+1}/{len(batches)} mapping...")
        _, batch_changed = apply_mapping(session, user_id, ai, None, None, batch_node_ids, raw_json=raw_json)
        if batch_changed:
            any_changes = True

    return any_changes

def cleanup_roots(session, user_id="default"):
    """Post-processing: merge duplicate roots, dissolve generic roots, mark tiny orphans."""
    logger.info("\n[Root Cleanup] Starting post-batch root cleanup...")
    
    # Pre-build node_map and children_map
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    node_map = {t.id: t for t in topics}
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)
        
    roots = [t for t in topics if t.parent_id is None]
    
    # Count descendants in-memory
    desc_count = {}
    for node in topics:
        desc_count[node.id] = 0
    for node in topics:
        curr = node_map.get(node.parent_id)
        while curr:
            desc_count[curr.id] = desc_count.get(curr.id, 0) + 1
            curr = node_map.get(curr.parent_id)
            
    # 1. Merge same-name roots (keep the one with most descendants)
    # Use fuzzy normalization to catch plural/singular and whitespace variants
    name_groups = defaultdict(list)
    for r in roots:
        name_groups[normalize_topic_name(r.name)].append(r)
    
    merge_count = 0
    for name, group in name_groups.items():
        if len(group) < 2:
            continue
        
        group.sort(key=lambda x: desc_count.get(x.id, 0), reverse=True)
        survivor = group[0]
        
        for dup in group[1:]:
            logger.info(f"  ROOT-MERGE: '{dup.name}' (ID {dup.id}, {desc_count.get(dup.id, 0)} descendants) INTO '{survivor.name}' (ID {survivor.id})")
            # Transfer children
            for child in list(dup.children):
                child.parent = survivor
            # Transfer memories
            for mem in list(dup.episodic_memories): mem.topic = survivor
            for mem in list(dup.user_memories): mem.topic = survivor
            for mem in list(dup.knowledge_memories): mem.topic = survivor
            for mem in list(dup.decision_memories): mem.topic = survivor
            # Delete node
            session.delete(dup)
            merge_count += 1
            
    any_changes = False
    if merge_count > 0:
        session.flush()
        logger.info(f"  Merged {merge_count} duplicate roots.")
        any_changes = True
        
    # 2. Identify problem roots (generic-named or tiny) — do NOT dissolve them.
    # relocate_problem_roots() will handle them via LLM after this function returns.
    session.expire_all()
    # Rebuild topics and roots after merges
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    node_map = {t.id: t for t in topics}
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)
    roots = [t for t in topics if t.parent_id is None]
    
    # Recalculate descendant counts
    desc_count = {}
    for node in topics:
        desc_count[node.id] = 0
    for node in topics:
        curr = node_map.get(node.parent_id)
        while curr:
            desc_count[curr.id] = desc_count.get(curr.id, 0) + 1
            curr = node_map.get(curr.parent_id)
            
    problem_count = 0
    for r in roots:
        d_count = desc_count.get(r.id, 0)
        is_generic = r.name.lower().strip() in GENERIC_NAMES
        is_tiny = d_count <= TINY_ROOT_THRESHOLD
        if is_generic or is_tiny:
            reason = "generic-named" if is_generic else f"tiny ({d_count} nodes)"
            logger.info(f"  PROBLEM-ROOT: '{r.name}' (ID {r.id}) — {reason} — queued for LLM relocation")
            problem_count += 1
    if problem_count:
        logger.info(f"  {problem_count} problem root(s) queued for relocate_problem_roots().")
        
    # 3. Mark tiny/generic roots as ungroomed so they surface in the next pass.
    tiny_count = 0
    for r in roots:
        total_nodes = desc_count.get(r.id, 0)
        if total_nodes <= TINY_ROOT_THRESHOLD or r.name.lower().strip() in GENERIC_NAMES:
            r.is_groomed = 0
            for desc in _get_all_descendants(r, children_map=children_map):
                desc.is_groomed = 0
            tiny_count += 1
            logger.info(f"  TINY-ROOT: '{r.name}' (ID {r.id}, {total_nodes} nodes) marked ungroomed for future placement")
            
    if tiny_count > 0:
        session.flush()
        logger.info(f"  Marked {tiny_count} tiny roots as ungroomed.")
    
    session.commit()
    logger.info("[Root Cleanup] Complete.")
    return any_changes

def relocate_problem_roots(session, user_id="default", node_map=None, children_map=None, mem_count_map=None):
    logger.info("\n PHASE 3: RELOCATE PROBLEM ROOTS")
    ai = SafeAI()
    embedder = EmbeddingManager()
    from mindcache.Database.db_manager import DatabaseManager

    session.expire_all()
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    
    # Rebuild maps to be safe with the latest state after cleanup_roots
    node_map = {t.id: t for t in topics}
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)
    parent_map = {t.id: node_map.get(t.parent_id) for t in topics}
    
    roots = [t for t in topics if t.parent_id is None]
    
    problem_roots = []
    for r in roots:
        desc_count = len(_get_all_descendants(r, children_map))
        is_generic = r.name.lower().strip() in GENERIC_NAMES
        is_tiny = desc_count <= TINY_ROOT_THRESHOLD
        if is_generic or is_tiny:
            problem_roots.append(r)
            
    if not problem_roots:
        logger.info("  No problem roots found. Skipping relocation pass.")
        return
        
    logger.info(f"  Found {len(problem_roots)} problem root(s) to relocate.")
    
    problem_root_ids = {r.id for r in problem_roots}
    
    # Find all leaves and categorize them
    all_leaves = [t for t in topics if len(children_map.get(t.id, [])) == 0]
    
    # 1. Ensure all leaves have embeddings
    to_embed = []
    leaf_vecs_map = {}
    
    for leaf in all_leaves:
        if leaf.embedding is not None:
            leaf_vecs_map[leaf.id] = DatabaseManager._from_blob(leaf.embedding)
        else:
            embed_text = DatabaseManager._build_leaf_embed_text(leaf, parent_map)
            to_embed.append((leaf, embed_text))
            
    if to_embed:
        logger.info(f"  Embedding {len(to_embed)} missing leaves (both healthy and problem)...")
        texts = [b[1] for b in to_embed]
        vecs = embedder.get_batch_embeddings(texts)
        for j, (leaf, _) in enumerate(to_embed):
            leaf.embedding = embedder._to_blob(vecs[j])
            leaf_vecs_map[leaf.id] = vecs[j]
        session.commit()
        
    # 2. Partition leaves
    problem_leaves = []
    healthy_leaves = []
    
    for leaf in all_leaves:
        # Find root of leaf
        curr = leaf
        while curr.parent_id is not None:
            curr = node_map.get(curr.parent_id)
        if curr.id in problem_root_ids:
            problem_leaves.append(leaf)
        else:
            healthy_leaves.append(leaf)
            
    if len(healthy_leaves) < 2:
        logger.info("  Not enough healthy leaves to cluster. Skipping relocation.")
        return
        
    # 3. Cluster healthy leaves
    logger.info(f"  Clustering {len(healthy_leaves)} healthy leaves...")
    healthy_vecs = [leaf_vecs_map[leaf.id] for leaf in healthy_leaves]
    labels, centroids = hnsw_leiden_cluster(np.vstack(healthy_vecs), target_size=300, max_cluster_size=500)
    K = len(np.unique(labels))
    logger.info(f"  Leiden determined K={K} healthy clusters.")
    similarity_matrix = np.dot(centroids, centroids.T)
    
    # 4. Assign problem roots to nearest cluster
    problem_root_assignments = defaultdict(list)  # cluster_id -> list of problem roots
    
    for r in problem_roots:
        r_descendants = set(n.id for n in _get_all_descendants(r, children_map))
        r_leaves = [l for l in problem_leaves if l.id in r_descendants]
        if not r_leaves:
            best_c = 0
        else:
            r_vecs = [leaf_vecs_map[l.id] for l in r_leaves]
            r_centroid = np.mean(r_vecs, axis=0)
            norm = np.linalg.norm(r_centroid)
            r_centroid = r_centroid / (norm if norm > 1e-10 else 1.0)
            
            sims = np.dot(centroids, r_centroid)
            best_c = np.argmax(sims)
            
        problem_root_assignments[best_c].append(r)
        logger.info(f"    Assigned problem root '{r.name}' to healthy cluster {best_c}")
        
    # 5. Token-budget-aware batching
    BATCH_TOKEN_BUDGET = TOKEN_LIMIT
    
    def _build_problem_tree_text(roots_list):
        text = ""
        for r in roots_list:
            text += build_branch_text(r, children_map=children_map, mem_count_map=mem_count_map) + "\n\n"
        return text
        
    cluster_token_cost = {}
    
    for c in range(K):
        healthy_txt = _build_cluster_tree_text({c}, healthy_leaves, labels, node_map, children_map, mem_count_map)
        assigned_roots = problem_root_assignments.get(c, [])
        problem_txt = _build_problem_tree_text(assigned_roots)
        cluster_token_cost[c] = estimate_tokens(healthy_txt + problem_txt)
        
    unassigned = set(range(K))
    batches = [] 
    
    while unassigned:
        un_list = list(unassigned)
        if len(un_list) == 1:
            batches.append([un_list[0]])
            break
            
        sub_matrix = similarity_matrix[np.ix_(un_list, un_list)]
        np.fill_diagonal(sub_matrix, -np.inf)
        idx = np.argmax(sub_matrix)
        r, c = np.unravel_index(idx, sub_matrix.shape)
        c1, c2 = un_list[r], un_list[c]
        
        base_c = c1 if cluster_token_cost[c1] > cluster_token_cost[c2] else c2
        unassigned.remove(base_c)
        batch_clusters = [base_c]
        batch_tokens = cluster_token_cost[base_c]
        
        while unassigned:
            un_list_remaining = list(unassigned)
            sims_to_batch = np.mean(similarity_matrix[np.ix_(un_list_remaining, batch_clusters)], axis=1)
            best_idx = np.argmax(sims_to_batch)
            best_u = un_list_remaining[best_idx]
            cost = cluster_token_cost[best_u]
            
            if batch_tokens + cost <= BATCH_TOKEN_BUDGET:
                batch_clusters.append(best_u)
                batch_tokens += cost
                unassigned.remove(best_u)
            else:
                break
        batches.append(batch_clusters)
        
    logger.info(f"  Formed {len(batches)} batches for problem root relocation.")
    
    system_prompt = "You are an expert knowledge architect. YOUR ABSOLUTE HIGHEST PRIORITY IS HANDLING ORPHAN/PROBLEM ROOTS. You must find proper homes for the problem roots in the stable ontology. Output valid JSON."
    
    futures = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for idx, batch_c in enumerate(batches):
            logger.info(f"    - Submitting Relocation Batch {idx+1}/{len(batches)} (Clusters: {batch_c})")
            futures.append(executor.submit(
                run_relocation_batch_llm, idx, batch_c, healthy_leaves, labels, node_map, children_map, mem_count_map, problem_root_assignments, ai, system_prompt
            ))
            
    any_changes = False
    for f in futures:
        idx, raw_json, batch_node_ids = f.result()
        logger.info(f"\n  Applying Relocation Batch {idx+1}/{len(batches)} mapping...")
        _, batch_changed = apply_mapping(session, user_id, ai, None, None, batch_node_ids, raw_json=raw_json)
        if batch_changed:
            any_changes = True
            
    return any_changes

def split_overloaded_leaves(session, user_id="default", max_limit=100):
    from sklearn.cluster import KMeans
    from mindcache.Database.embedder import EmbeddingManager
    from pydantic import BaseModel, Field

    class ClusterNaming(BaseModel):
        cluster_index: int = Field(description="The index of the cluster (1-based index corresponding to the prompt).")
        name: str = Field(description="Name of the sub-category.")
        placement: str = Field(description="'child' if this should be a child of the current category, or 'sibling' if it should replace the current category.")

    class ClusterSplitSchema(BaseModel):
        sub_categories: List[ClusterNaming] = Field(description="List of names and placements for the clusters.")

    logger.info(f"\n PHASE 0: SPLIT OVERLOADED LEAVES (>{max_limit} memories)")
    ai = SafeAI()
    embedder = EmbeddingManager()
    
    session.expire_all()
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)
        
    leaves = [t for t in topics if not children_map.get(t.id)]
    
    counts = _precompute_memory_counts(session, user_id=user_id)
    overloaded_leaves = []
    for leaf in leaves:
        if counts.get(leaf.id, 0) > max_limit:
            memories = leaf.episodic_memories + leaf.user_memories + leaf.knowledge_memories + leaf.decision_memories
            overloaded_leaves.append((leaf, memories))
            
    if not overloaded_leaves:
        logger.info("  No overloaded leaves found.")
        return
        
    logger.info(f"  Found {len(overloaded_leaves)} overloaded leaves to split.")
    
    for leaf, memories in overloaded_leaves:
        logger.info(f"  Splitting leaf '{leaf.name}' (ID {leaf.id}) with {len(memories)} memories...")
        
        # Load or generate embeddings for these memories
        embeddings_list = []
        valid_memories = []
        mems_to_embed = []
        for m in memories:
            if m.embedding is not None:
                embeddings_list.append(to_numpy(m.embedding))
                valid_memories.append(m)
            else:
                mems_to_embed.append(m)
                
        if mems_to_embed:
            logger.info(f"    Generating embeddings for {len(mems_to_embed)} memories...")
            texts = [m.content for m in mems_to_embed]
            vecs = embedder.get_batch_embeddings(texts)
            for j, m in enumerate(mems_to_embed):
                m.embedding = embedder._to_blob(vecs[j])
                embeddings_list.append(vecs[j])
                valid_memories.append(m)
            session.commit()
            
        if not embeddings_list:
            continue
            
        embeddings_matrix = np.vstack(embeddings_list)
        
        # Determine K
        K = max(2, min(5, len(valid_memories) // 30))
        logger.info(f"    Clustering {len(valid_memories)} memories into {K} sub-categories...")
        
        kmeans = KMeans(n_clusters=K, random_state=42, n_init='auto')
        cluster_labels = kmeans.fit_predict(embeddings_matrix)
        centroids = kmeans.cluster_centers_
        
        cluster_memories = defaultdict(list)
        for idx, label in enumerate(cluster_labels):
            cluster_memories[label].append(valid_memories[idx])
            
        cluster_representatives = defaultdict(list)
        for label in range(K):
            c_memories = cluster_memories[label]
            c_idx = np.where(cluster_labels == label)[0]
            c_embeddings = embeddings_matrix[c_idx]
            centroid = centroids[label]
            
            distances = np.linalg.norm(c_embeddings - centroid, axis=1)
            closest_indices = np.argsort(distances)[:8]
            cluster_representatives[label] = [c_memories[i] for i in closest_indices]
            
        # Prompt LLM
        prompt_text = f"Parent Category: {leaf.name}\n\n"
        for label in range(K):
            prompt_text += f"Cluster {label+1} Representative Memories:\n"
            for m in cluster_representatives[label]:
                prompt_text += f"- {m.content}\n"
            prompt_text += "\n"
            
        system_prompt = (
            "You are an expert knowledge architect. "
            "Your task is to split a large, overloaded category into smaller, more specific sub-categories. "
            "We have clustered the memories of the category mathematically. For each cluster index, you must output:\n"
            "1. name: A clean, specific name for the sub-category.\n"
            "2. placement: 'child' if the sub-category should be a child of the parent, or 'sibling' if it should replace the parent.\n"
            "Use only the cluster_index from the prompt (1-based index).\n"
            "Output valid JSON conforming to the ClusterSplitSchema."
        )
        
        try:
            raw_response = ai.generate(
                prompt=prompt_text,
                system_prompt=system_prompt,
                json_schema=ClusterSplitSchema.model_json_schema()
            )
            
            if raw_response is None:
                logger.info("      LLM returned None. Skipping this split.")
                continue
                
            data = json.loads(raw_response)
            split_data = ClusterSplitSchema(**data)
            
            all_sibling_placements = True
            mapped_clusters = {sub.cluster_index - 1 for sub in split_data.sub_categories if 0 <= sub.cluster_index - 1 < K}
            if len(mapped_clusters) < K:
                logger.warning(f"      [Warning] LLM omitted {K - len(mapped_clusters)} cluster(s). Original leaf '{leaf.name}' (ID {leaf.id}) will be kept.")
                all_sibling_placements = False

            for sub in split_data.sub_categories:
                cluster_idx = sub.cluster_index - 1
                if cluster_idx < 0 or cluster_idx >= K:
                    continue
                if sub.placement != 'sibling':
                    all_sibling_placements = False
                    
                new_topic = Topic(
                    name=normalize_topic_name(sub.name),
                    level=leaf.level + 1 if sub.placement == 'child' else leaf.level,
                    parent_id=leaf.id if sub.placement == 'child' else leaf.parent_id,
                    user_id=user_id
                )
                session.add(new_topic)
                session.flush()
                
                moved_count = 0
                for m in cluster_memories[cluster_idx]:
                    m.topic_id = new_topic.id
                    moved_count += 1
                    
                logger.info(f"      Created {sub.placement} '{new_topic.name}' (ID {new_topic.id}) with {moved_count} memories.")
                
            if all_sibling_placements:
                logger.info(f"    All memories moved to siblings. Deleting empty original leaf '{leaf.name}' (ID {leaf.id}).")
                session.delete(leaf)
                
            session.commit()
            
        except Exception as e:
            logger.info(f"      Failed to split leaf {leaf.name}: {e}")
            session.rollback()

def reorganize_tree(user_id="default", dry_run=True):
    SessionLocal = sessionmaker(bind=Session.kw['bind'], expire_on_commit=False)
    session = SessionLocal()
    try:
        # 0. Check and split overloaded leaves before doing anything else
        split_overloaded_leaves(session, user_id=user_id, max_limit=100)

        # 1. Build in-memory maps FIRST — avoids N+1 ORM queries and guarantees
        #    that full_tree_text, children_map, and node_map are all consistent snapshots.
        children_map = defaultdict(list)
        topic_by_id = {None: None}
        topics = session.query(Topic).filter(Topic.user_id == user_id).all()
        for t in topics:
            topic_by_id[t.id] = t
            children_map[t.parent_id].append(t)
        parent_map = {t.id: topic_by_id.get(t.parent_id) for t in topics}

        # Pre-compute memory counts per node for richer tree text
        mem_count_map = _precompute_memory_counts(session, user_id=user_id)

        # Build tree text using in-memory maps (no N+1) with \n\n root separator
        all_roots = [t for t in topics if t.parent_id is None]
        full_tree_text = ""
        for r in all_roots:
            full_tree_text += build_branch_text(
                r, children_map=children_map, mem_count_map=mem_count_map
            ) + "\n\n"
        total_tokens = estimate_tokens(full_tree_text)

        logger.info(f"Total graph tokens estimated: {total_tokens}/{TOKEN_LIMIT}")
        logger.info(f"Roots: {len(all_roots)}, Total nodes: {len(topics)}")

        changed = False
        if total_tokens < TOKEN_LIMIT:
            _, pass_changed = pass_global_bootstrap(session, user_id, full_tree_text, children_map=children_map, node_map=topic_by_id)
            if pass_changed:
                changed = True
        else:
            pass_changed = kmeans_dedup_pass(session, user_id, children_map=children_map, node_map=topic_by_id, mem_count_map=mem_count_map)
            if pass_changed:
                changed = True

        cleanup_changed = cleanup_roots(session, user_id=user_id)  # Merges same-name duplicates and marks problem roots
        if cleanup_changed:
            changed = True

        # ── SECOND PASS ──────────────────────────────────────────────────────────
        # After the first pass commits structural changes, re-measure the tree and
        # run another dedup/reorganization round.
        if not changed:
            logger.info("[Second pass] Skipping because no changes were made in the first pass.")
        else:
            session.expire_all()
            topics2 = session.query(Topic).filter(Topic.user_id == user_id).all()
            topic_by_id2 = {None: None}
            children_map2 = defaultdict(list)
            for t in topics2:
                topic_by_id2[t.id] = t
                children_map2[t.parent_id].append(t)
            mem_count_map2 = _precompute_memory_counts(session, user_id=user_id)
            all_roots2 = [t for t in topics2 if t.parent_id is None]
            full_tree_text2 = ""
            for r in all_roots2:
                full_tree_text2 += build_branch_text(
                    r, children_map=children_map2, mem_count_map=mem_count_map2
                ) + "\n\n"
            total_tokens2 = estimate_tokens(full_tree_text2)
            logger.info(f"[Second pass] Tree tokens after first pass: {total_tokens2}/{TOKEN_LIMIT}")

            if total_tokens2 < TOKEN_LIMIT:
                pass_global_bootstrap(session, user_id, full_tree_text2, children_map=children_map2, node_map=topic_by_id2)
            else:
                kmeans_dedup_pass(session, user_id, children_map=children_map2, node_map=topic_by_id2, mem_count_map=mem_count_map2)

            relocate_problem_roots(session, user_id=user_id, node_map=topic_by_id2, children_map=children_map2, mem_count_map=mem_count_map2)
            collapse_single_child_chains(session, user_id=user_id)
            cleanup_stale_source_maps(session, user_id=user_id)  # Purge deleted IDs from parent source_maps

    finally:
        session.close()
        logger.info("\nAll grooming completed successfully.")


def choose_name(p_name, c_name):
    # Choose the better/more specific name between parent and child
    p_norm = p_name.strip()
    c_norm = c_name.strip()
    
    # Rule 1: If one is "General X" and the other is "X" (or vice versa), keep the one without "General"
    p_no_gen = re.sub(r'(?i)^general\s+', '', p_norm).strip()
    c_no_gen = re.sub(r'(?i)^general\s+', '', c_norm).strip()
    
    if p_no_gen.lower() == c_no_gen.lower():
        if p_norm.lower().startswith("general"):
            return c_norm
        else:
            return p_norm

    # Rule 2: If child starts with "General" but parent does not
    if c_norm.lower().startswith("general ") and not p_norm.lower().startswith("general "):
        return p_norm
    if p_norm.lower().startswith("general ") and not c_norm.lower().startswith("general "):
        return c_norm
        
    # Rule 3: If one is a substring of another, keep the longer/more specific one
    if p_norm.lower() in c_norm.lower():
        return c_norm
    if c_norm.lower() in p_norm.lower():
        return p_norm
        
    # Rule 4: Default to child's name because it represents the deeper/more specific sub-level
    return c_norm


def collapse_single_child_chains(session, user_id="default"):
    """
    Find internal nodes with exactly 1 child, and collapse them.
    Moves children/memories of the child to the parent, updates metadata, and deletes the child.
    """
    topics = session.query(Topic).filter(Topic.user_id == user_id).all()
    node_map = {t.id: t for t in topics}
    children_map = defaultdict(list)
    for t in topics:
        children_map[t.parent_id].append(t)
        
    collapsed = 0
    memories_moved = 0
    children_moved = 0
    
    worklist = [t for t in topics if len(children_map[t.id]) == 1]
    deleted_ids = set()
    
    while worklist:
        parent = worklist.pop()
        if parent.id in deleted_ids:
            continue
            
        child_list = children_map[parent.id]
        if len(child_list) != 1:
            continue
            
        child = child_list[0]
        if child.id in deleted_ids:
            continue
            
        # Choose the better name
        chosen_name = choose_name(parent.name, child.name)
        logger.info(f"  [Collapse] Collapsing '{child.name}' (ID {child.id}) into parent '{parent.name}' (ID {parent.id}) -> Chosen Name: '{chosen_name}'")
        
        # Update parent name
        parent.name = chosen_name
        
        # Reparent grandchildren to parent
        grandchildren = children_map.get(child.id, [])
        for grandchild in list(grandchildren):
            grandchild.parent = parent
            children_moved += 1
            
        # Update children_map: parent's children is now grandchildren
        children_map[parent.id] = list(grandchildren)
        
        # Move memories to parent
        for mem in list(child.episodic_memories):
            mem.topic = parent
            memories_moved += 1
        for mem in list(child.user_memories):
            mem.topic = parent
            memories_moved += 1
        for mem in list(child.knowledge_memories):
            mem.topic = parent
            memories_moved += 1
        for mem in list(child.decision_memories):
            mem.topic = parent
            memories_moved += 1
            
        transfer_topic_metadata(child, parent)
        
        # Delete caches (TopicEmbeddingCache removed)
        
        session.delete(child)
        deleted_ids.add(child.id)
        collapsed += 1
        
        if len(children_map[parent.id]) == 1:
            worklist.append(parent)
            
    if collapsed > 0:
        session.commit()
        logger.info(f"  [Collapse] Collapsed {collapsed} single-child nodes. Reparented {children_moved} grandchildren. Moved {memories_moved} memories.")
    else:
        logger.info("  [Collapse] No single-child wrapper nodes found.")


def cleanup_stale_source_maps(session, user_id="default"):
    """
    After tree reorganization, purge deleted node IDs from all parent source_maps
    and ignored_ids. Prevents ghost references from corrupting retrieval.
    """
    all_valid_ids = set(str(t.id) for t in session.query(Topic.id).filter(Topic.user_id == user_id).all())
    parents = [t for t in session.query(Topic).filter(Topic.user_id == user_id).all() if t.children]
    
    cleaned = 0
    for parent in parents:
        if not parent.summary or not parent.summary.startswith("{"):
            continue
        try:
            state = json.loads(parent.summary)
        except:
            continue
        
        source_map = state.get("source_map", {})
        ignored_ids = state.get("ignored_ids", [])
        
        # Find stale IDs
        stale_source = [k for k in source_map if k not in all_valid_ids]
        stale_ignored = [x for x in ignored_ids if str(x) not in all_valid_ids]
        
        # Also check: source_map IDs that aren't actual children of this parent
        actual_child_ids = set(str(c.id) for c in parent.children)
        orphan_source = [k for k in source_map if k not in actual_child_ids]
        orphan_ignored = [x for x in ignored_ids if str(x) not in actual_child_ids]
        
        if not stale_source and not stale_ignored and not orphan_source and not orphan_ignored:
            continue
        
        # Remove stale and orphan entries
        for k in set(stale_source + orphan_source):
            if k in source_map:
                del source_map[k]
        
        new_ignored = [x for x in ignored_ids if str(x) not in 
                       set(str(s) for s in stale_ignored + orphan_ignored)]
        
        state["source_map"] = source_map
        state["ignored_ids"] = new_ignored
        parent.summary = json.dumps(state)
        parent.description = "\n".join(source_map.values())
        parent.embedding = None  # Force re-embedding
        session.add(parent)
        cleaned += 1
        
        removed = len(stale_source) + len(orphan_source) + len(stale_ignored) + len(orphan_ignored)
        logger.info(f"  [SourceMap] Cleaned '{parent.name}': removed {removed} stale/orphan IDs")
    
    if cleaned:
        session.commit()
        logger.info(f"  [SourceMap] Cleaned {cleaned} parent source_maps")
    else:
        logger.info("  [SourceMap] All parent source_maps are clean")

if __name__ == "__main__":
    dry_run = False
    reorganize_tree(dry_run=dry_run)