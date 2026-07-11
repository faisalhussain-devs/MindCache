import json
from datetime import datetime
from sqlalchemy import func
from mindcache.Database.db_setup import Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory, Session
from mindcache.Memory_extract.summary_extractor import Summary_Extractor
from sqlalchemy.orm import sessionmaker
import logging
logger = logging.getLogger(__name__)

MAJOR_UPDATE_THRESHOLD = 60
FULL_REBUILD_THRESHOLD = 10       # parent nodes: full rebuild after N incremental updates
LEAF_DESC_REBUILD_THRESHOLD = 10  # leaf nodes:   force full description rebuild after N updates

class RecursiveSummarizer:
    def __init__(self):
        self.Session = Session
        self.extractor = Summary_Extractor()
    
    def _build_search_text(self, summary_data):
        """Convert structured summary to plain text for embedding/BM25."""
        lines = []
        for _, mems in summary_data.get("memories", {}).items():
            for content in mems.values():
                lines.append(content)  # Already has [Episodic]/[Knowledge] prefix
        for _, decs in summary_data.get("decisions", {}).items():
            for content in decs.values():
                lines.append(content)  # Already has [Decision:status] prefix
        return "\n".join(lines)

    def get_max_depth(self, session):
        result = session.query(func.max(Topic.level)).scalar()
        return result or 0

    def get_leaf_summary(self, session, topic_id, min_timestamp=0):
        """
        Fetches memories for this topic across all 4 memory types.
        Returns a dict with two keys:
          "memories": { "YYYY-MM-DD HH:MM": { id: "[Type] content", ... } }
          "decisions": { "YYYY-MM-DD HH:MM": { id: "[Decision:status] content | Context: reasoning", ... } }
        Memories ordered by timestamp. Decisions ordered by last_validated_at.
        """
        # --- Non-decision memories (ordered by timestamp) ---
        non_decision_models = [
            (EpisodicMemory, 'Episodic'),
            (UserMemory, 'User'),
            (KnowledgeMemory, 'Knowledge'),
        ]
        min_dt = datetime.fromtimestamp(min_timestamp) if min_timestamp != 0 else datetime.min
        raw_entries = []
        for Model, type_name in non_decision_models:
            query = session.query(Model).filter(Model.topic_id == topic_id)
            if min_timestamp > 0:
                query = query.filter(Model.timestamp > min_dt)
            for mem in query.all():
                if mem.content:
                    raw_entries.append((
                        mem.timestamp if mem.timestamp else datetime.min,
                        mem.id,
                        type_name,
                        mem.content
                    ))

        memories_dict = {}
        for ts, mem_id, mem_type, content in raw_entries:
            ts_key = ts.strftime('%Y-%m-%d %H:%M')
            if ts_key not in memories_dict:
                memories_dict[ts_key] = {}
            memories_dict[ts_key][mem_id] = f"[{mem_type}] {content}"

        # --- Decision memories (ordered by timestamp) ---
        dec_query = session.query(DecisionMemory).filter(
            DecisionMemory.topic_id == topic_id
        ).order_by(DecisionMemory.timestamp.desc())
        if min_timestamp > 0:
            dec_query = dec_query.filter(DecisionMemory.timestamp > min_dt)

        decisions_dict = {}
        for dec in dec_query.all():
            if not dec.content:
                continue
            ts_key = (dec.timestamp or datetime.min).strftime('%Y-%m-%d %H:%M')
            if ts_key not in decisions_dict:    
                decisions_dict[ts_key] = {}
            ctx = f" | Context: {dec.context}" if dec.context else ""
            validated_str = f" | Last validated: {dec.last_validated_at.strftime('%Y-%m-%d') if dec.last_validated_at else 'never'}"
            decisions_dict[ts_key][dec.id] = f"[Decision:{dec.status}] {dec.content}{ctx}{validated_str}"

        if not memories_dict and not decisions_dict:
            return {}

        return {"memories": memories_dict, "decisions": decisions_dict}

    def _format_leaf_summary(self, summary_data, latest_first=True):
        """
        Converts the leaf summary data into a readable string for LLM prompts.
        Handles both old format (flat dict) and new format ({memories, decisions}).
        """
        if not summary_data:
            return ""
        
        # Handle new format with separate memories and decisions
        if isinstance(summary_data, dict) and ("memories" in summary_data or "decisions" in summary_data):
            memories_dict = summary_data.get("memories", {})
            decisions_dict = summary_data.get("decisions", {})
            
            lines = []
            
            # Format regular memories
            if memories_dict:
                if latest_first:
                    mem_keys = sorted(list(memories_dict.keys()), reverse=True)
                else:
                    mem_keys = sorted(list(memories_dict.keys()))
                for ts_key in mem_keys:
                    for mem_id, content in memories_dict[ts_key].items():
                        lines.append(f"[{ts_key}] (#{mem_id}) {content}")
            
            # Format decisions separately
            if decisions_dict:
                lines.append("\n DECISIONS")
                if latest_first:
                    dec_keys = sorted(list(decisions_dict.keys()), reverse=True)
                else:
                    dec_keys = sorted(list(decisions_dict.keys()))
                for ts_key in dec_keys:
                    for dec_id, content in decisions_dict[ts_key].items():
                        lines.append(f"[validated: {ts_key}] (#{dec_id}) {content}")
            
            return "\n".join(lines)

    def process_leaf(self, session, node):
        """
        Processes database checks and memory/decision updates for a leaf node.
        Returns the raw_desc search text if the leaf node has new data and needs 
        an LLM summary; otherwise returns None.
        """
        existing_summary = {"memories": {}, "decisions": {}}
        min_timestamp = 0
        try:
            if node.summary and node.summary.startswith("{"):
                parsed = json.loads(node.summary)
                if "memories" in parsed or "decisions" in parsed:
                    existing_summary = parsed
                    all_keys = list(parsed.get("memories", {}).keys()) + list(parsed.get("decisions", {}).keys())
                    if all_keys:
                        max_date_str = max(all_keys)
                        min_timestamp = datetime.strptime(max_date_str, '%Y-%m-%d %H:%M').timestamp() + 1
                else:
                    raise ValueError("Error: Invalid summary format")
        except Exception as e:
            logger.error(f"Error: {e}")
            existing_summary = {"memories": {}, "decisions": {}}

        new_data = self.get_leaf_summary(session, node.id, min_timestamp=min_timestamp)
        if not new_data:
            return None
        new_memories = new_data.get("memories", {})
        new_decisions = new_data.get("decisions", {})
        has_new = False

        # Merge memories and detect exactly what is new (ID-based instead of time-based)
        if new_memories:
            for ts_key, mem_dict in new_memories.items():
                if ts_key not in existing_summary["memories"]:
                    existing_summary["memories"][ts_key] = {}
                for mid, content in mem_dict.items():
                    mid_str = str(mid)
                    if mid_str not in existing_summary["memories"][ts_key]:
                        existing_summary["memories"][ts_key][mid_str] = content
                        has_new = True
        # Merge decisions
        if new_decisions:
            for ts_key, dec_dict in new_decisions.items():
                if ts_key not in existing_summary["decisions"]:
                    existing_summary["decisions"][ts_key] = {}
                for did, content in dec_dict.items():
                    did_str = str(did)
                    if did_str not in existing_summary["decisions"][ts_key] or existing_summary["decisions"][ts_key][did_str] != content:
                        existing_summary["decisions"][ts_key][did_str] = content
                        has_new = True
        if has_new:
            existing_summary["desc_dirty"] = True
            node.summary = json.dumps(existing_summary)
            return self._build_search_text(existing_summary)
        return None
    
    def process_leaves_batched(self, session, leaf_nodes):
        logger.info(f"\n--- Checking {len(leaf_nodes)} Leaf Nodes for Updates ---")
        
        # Precompute counts
        counts = {}
        for model in [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]:
            res = session.query(model.topic_id, func.count(model.id)).group_by(model.topic_id).all()
            for topic_id, count in res:
                if topic_id is not None:
                    counts[topic_id] = counts.get(topic_id, 0) + count

        to_create = []
        to_update = []
        skipped   = 0

        for node in leaf_nodes:
            total_mems = counts.get(node.id, 0)

            if total_mems <= 50:
                skipped += 1
                continue
            raw_desc = self.process_leaf(session, node)

            if not (raw_desc or "").strip():
                existing_desc = (node.description or "").strip()
                if node.summary and node.summary.startswith("{"):
                    try:
                        stored = json.loads(node.summary)
                        if stored.get("desc_dirty") and (stored.get("memories") or stored.get("decisions")):
                            recovered = self._build_search_text(stored)
                            if (recovered or "").strip():
                                if not existing_desc:
                                    logger.info(f"  [Leaf] '{node.name}' — no description, dirty summary. Recovering (create).")
                                    to_create.append({"node": node, "raw_desc": recovered})
                                else:
                                    desc_update_count = stored.get("desc_update_count", 0)
                                    logger.info(f"  [Leaf] '{node.name}' — description stale (dirty flag). Recovering (update).")
                                    if desc_update_count >= LEAF_DESC_REBUILD_THRESHOLD:
                                        to_create.append({"node": node, "raw_desc": recovered, "rebuild": True})
                                    else:
                                        to_update.append({
                                            "node": node,
                                            "raw_desc": recovered,
                                            "existing_desc": existing_desc,
                                            "desc_update_count": desc_update_count,
                                        })
                                continue
                    except Exception:
                        pass
                continue

            desc_update_count = 0
            try:
                if node.summary and node.summary.startswith("{"):
                    desc_update_count = json.loads(node.summary).get("desc_update_count", 0)
            except Exception:
                pass

            existing_desc = (node.description or "").strip()

            if not existing_desc:
                to_create.append({"node": node, "raw_desc": raw_desc})

            elif desc_update_count >= LEAF_DESC_REBUILD_THRESHOLD:
                logger.info(f"  [Leaf] '{node.name}' hit {LEAF_DESC_REBUILD_THRESHOLD} updates "
                      f"— forcing full description rebuild.")
                full_raw = self._build_search_text(
                    json.loads(node.summary) if node.summary and node.summary.startswith("{") else {}
                )
                to_create.append({"node": node, "raw_desc": full_raw or raw_desc, "rebuild": True})

            else:
                to_update.append({
                    "node": node,
                    "raw_desc": raw_desc,
                    "existing_desc": existing_desc,
                    "desc_update_count": desc_update_count,
                })

        if skipped:
            logger.info(f"  {skipped} leaf nodes skipped (≤50 memories, no summary needed).")

        if not to_create and not to_update:
            logger.info("  No leaf nodes require LLM summary updates.")
            return

        if to_create:
            rebuilds = sum(1 for x in to_create if x.get("rebuild"))
            fresh    = len(to_create) - rebuilds
            parts    = []
            if fresh:    parts.append(f"{fresh} fresh")
            if rebuilds: parts.append(f"{rebuilds} forced rebuild")
            logger.info(f"  Create path: {', '.join(parts)}.")
        if to_update:
            logger.info(f"  {len(to_update)} leaf nodes need description update (update).")

        MAX_BATCH_CHARS = 8000

        def _make_batches(items, key="raw_desc"):
            batches, cur, cur_len = [], [], 0
            for item in items:
                item_len = len(item[key])
                if cur and cur_len + item_len > MAX_BATCH_CHARS:
                    batches.append(cur)
                    cur, cur_len = [item], item_len
                else:
                    cur.append(item)
                    cur_len += item_len
            if cur:
                batches.append(cur)
            return batches

        # 1. CREATE batches (fresh + forced rebuilds
        create_batches = _make_batches(to_create)
        logger.info(f"  Create split into {len(create_batches)} batch(es).")

        for idx, batch in enumerate(create_batches):
            logger.info(f"  [Create Batch {idx+1}/{len(create_batches)}] Summarizing {len(batch)} nodes...")

            prompt = """You are generating concise, high-fidelity summaries for multiple leaf topics.
                For each topic, build a concise, synthesized summary of its memories and decisions.
                - Do NOT use source map JSON, source IDs, or structured JSON. Just return a plain text summary.
                - Capture key facts, nuances, examples, and decisions in a direct, cohesive summary.
                - Keep each summary relatively short and focused.
                - Avoid introducing any external context.

                Topics to summarize (formatted as ID: Name: Raw Context):
                """
            for item in batch:
                n = item["node"]
                prompt += f"\n--- TOPIC ID: {n.id} ({n.name}) ---\n{item['raw_desc']}\n"

            prompt += """
                Output a JSON object mapping each integer Topic ID to its plain text summary:
                {
                "12": "Summary of topic 12...",
                "15": "Summary of topic 15..."
                }
                """
            self._run_summary_batch(session, batch, prompt, mode="create")

        # 2. UPDATE batches
        update_batches = _make_batches(to_update)
        logger.info(f"  Update split into {len(update_batches)} batch(es).")

        for idx, batch in enumerate(update_batches):
            logger.info(f"  [Update Batch {idx+1}/{len(update_batches)}] Updating {len(batch)} nodes...")

            prompt = """You are UPDATING existing summaries for leaf topics that have received new memories.
                For each topic you will receive:
                - EXISTING SUMMARY: the current description (already correct and condensed)
                - NEW MEMORIES: raw memories added since the last summary

                Your task:
                - Integrate the new memories into the existing summary.
                - Preserve all information from the existing summary that is still relevant.
                - Remove or update anything that the new memories contradict or supersede.
                - Keep the final summary concise and cohesive.
                - Do NOT use source IDs or structured JSON in the output — plain text only.
                - If the new memories add nothing meaningfully new, return the existing summary unchanged.

                Topics to update:
                """
            for item in batch:
                n = item["node"]
                prompt += (
                    f"\n--- TOPIC ID: {n.id} ({n.name}) ---\n"
                    f"EXISTING SUMMARY:\n{item['existing_desc']}\n\n"
                    f"NEW MEMORIES:\n{item['raw_desc']}\n"
                )

            prompt += """
                Output a JSON object mapping each integer Topic ID to its updated plain text summary:
                {
                "12": "Updated summary of topic 12...",
                "15": "Updated summary of topic 15..."
                }
                """
            self._run_summary_batch(session, batch, prompt, mode="update")

    def _run_summary_batch(self, session, batch, prompt, mode="create"):
        """
        Calls the LLM with `prompt`, parses the JSON response, and writes
        description/timestamp/embedding back to each node in `batch`.

        Also maintains desc_update_count inside node.summary JSON:
          - mode="create" → resets count to 0 (fresh or rebuilt description)
          - mode="update" → increments count by 1

        On error, logs and skips (does NOT blank out an existing description).
        """
        response_json = None
        try:
            response_json = self.extractor.summary_extract(prompt)
            data = {}
            if response_json:
                if isinstance(response_json, str):
                    clean_json = response_json.replace("```json", "").replace("```", "")
                    s = clean_json.find("{")
                    e = clean_json.rfind("}")
                    if s != -1 and e != -1:
                        clean_json = clean_json[s:e+1]
                        data = json.loads(clean_json)
                    elif len(batch) == 1:
                        data = {str(batch[0]["node"].id): response_json.strip()}
                    else:
                        raise ValueError("No JSON object found in response.")
                else:
                    data = response_json

            for item in batch:
                n = item["node"]
                summary_text = data.get(str(n.id)) or data.get(n.id)
                if summary_text:
                    n.description = summary_text.strip()
                    n.timestamp   = datetime.now()
                    n.embedding   = None

                    # Update desc_update_count and clear desc_dirty in node's summary JSON
                    try:
                        summary_data = json.loads(n.summary) if n.summary and n.summary.startswith("{") else {}
                        if mode == "create":
                            summary_data["desc_update_count"] = 0   # fresh start
                        else:
                            summary_data["desc_update_count"] = summary_data.get("desc_update_count", 0) + 1
                        summary_data["desc_dirty"] = False  # description is now in sync
                        n.summary = json.dumps(summary_data)
                    except Exception:
                        pass  # non-critical; don't block the description write

                    session.add(n)
                else:
                    logger.warning(f"    [Warning] ({mode}) Summary missing for node '{n.name}' (ID {n.id}). Skipping.")
                    # Do NOT blank description — leave existing value intact.
            session.commit()

        except Exception as e:
            debug_resp = str(response_json)[:500] + "..." if response_json and len(str(response_json)) > 500 else str(response_json)
            logger.error(f"    [Error] Failed processing {mode} batch: {e}.")
            logger.error(f"    [Error] Raw LLM Response: {debug_resp}")
            logger.error("    [Error] Skipping batch — existing descriptions left intact.")
            # Do NOT blank descriptions on error; leave all nodes unchanged.

        
    def process_parent(self, session, node, max_depth=None):
        """
        Parent Node (Source-Map Architecture): 
        - Summary: JSON Blob { "source_map": { "id": "Contextual Summary" }, "ignored_ids": [id...] }
        """
        children = node.children
        if not children: return
        
        if len(children) == 1:
            child = children[0]
            node.summary = child.summary
            node.timestamp = child.timestamp
            node.embedding = None
            node.description = child.description
            session.add(node)
            return

        total_child_text_len = 0
        for child in children:
            if child.description:
                total_child_text_len += len(child.description)
            elif child.summary:
                total_child_text_len += len(child.summary)
            else:
                total_child_text_len += len(child.name or "")

        sorted_children = sorted(children, key=lambda x: x.timestamp or datetime.min, reverse=True)
        try:
            if node.summary and node.summary.startswith("{"):
                state = json.loads(node.summary)
            else:
                state = {}
        except:
            state = {}

        source_map = state.get("source_map", {})
        ignored_ids = set()
        for x in state.get("ignored_ids", []):
            try:
                ignored_ids.add(int(x))
            except (ValueError, TypeError):
                pass
                
        updates_count = state.get("updates_count", 0)
        was_cold_start = False
        if updates_count >= FULL_REBUILD_THRESHOLD:
            logger.info(f"  [Parent] '{node.name}' reached {FULL_REBUILD_THRESHOLD} updates — forcing full rebuild.")
            node.summary = None
            state = {}
            source_map = {}
            ignored_ids = set()
            updates_count = 0
            was_cold_start = True
        
        updates_batches = []
        current_batch = []
        current_len = 0
        MAX_CHAR_LIMIT = 28000

        node_ts = node.timestamp or datetime.min + 60

        for child in sorted_children:
            child_ts = child.timestamp or datetime.min
            is_new = str(child.id) not in source_map and child.id not in ignored_ids
            if is_new or child_ts > node_ts:
                status = "NEW" if is_new else ("EXISTING_SOURCE" if str(child.id) in source_map else "IGNORED")
                child_content = ""
                if not child.children:
                    # Use description (ID-free text) to avoid LLM confusing memory IDs with node IDs
                    if child.description and len(child.description.strip()) > 16:
                        child_content = child.description
                    else:
                        # Fallback: format from summary but strip memory IDs
                        try:
                            leaf_data = json.loads(child.summary) if child.summary else {}
                            child_content = self._format_leaf_summary(leaf_data)
                        except:
                            child_content = str(child.summary or child.name or "")
                else:
                    child_content = child.description or child.name or ""

                if status == "IGNORED" and len(child_content) < MAJOR_UPDATE_THRESHOLD:
                    continue
                    
                update_str = f"ID {child.id} ({child.name}): {child_content}"
                if current_len + len(update_str) > MAX_CHAR_LIMIT and current_batch:
                    updates_batches.append(current_batch)
                    current_batch = [update_str]
                    current_len = len(update_str)
                else:
                    current_batch.append(update_str)
                    current_len += len(update_str)
        if current_batch:
            updates_batches.append(current_batch)

        if not updates_batches and source_map:
            return 
        all_success = True

        if max_depth is None:
            max_depth = self.get_max_depth(session)

        if max_depth > 0:
            relative_depth = node.level / max_depth  # 0.0 = root, 1.0 = deepest parent
        else:
            relative_depth = 1.0

        if relative_depth >= 0.7:
            depth_tier = "near_leaf"
        elif relative_depth >= 0.3:
            depth_tier = "mid_level"  
        else:
            depth_tier = "high_level"

        if depth_tier == "near_leaf":
            tier_instruction = "High-fidelity compression. Preserve key facts, nuances, examples, and exceptions from each child. This summary is close to raw memories — capture specifics."
        elif depth_tier == "mid_level":
            tier_instruction = "Moderate abstraction. Focus on major findings, recurring themes, and important relationships across children. Compress repetitive detail but preserve the thematic landscape."
        else: # high_level
            tier_instruction = "Retrieval-support summary. Your job is NOT to compress the subtree proportionally. Your job is to ADVERTISE all concepts below, especially rare and small ones. A child with 5 memories is at higher retrieval risk than one with 500 — give the rare child MORE summary space. Compress dominant topics aggressively; preserve rare branches, edge cases, and unusual concepts."

        retrieval_risk_directive = """RETRIEVAL RISK ALLOCATION:
- Do NOT allocate summary space proportional to child memory count.
- Large children (many memories) are already well-represented by vector search.
- Small children (few memories) are at risk of being missed entirely.
- Give DISPROPORTIONATELY MORE space to small, rare, and unusual children.
- Compress well-known, large-memory children into brief mentions."""

        for batch in updates_batches:
            child_text = "\n".join(batch)
            if not source_map and not ignored_ids:
                prompt = f"""
You are building a hierarchical knowledge summary for the parent topic: '{node.name}'.

Child Nodes (these are the ONLY nodes you may reference — by their integer IDs):
{child_text}

Your task: Build a Source Map — a semantic compression of the subtree.

This is a HIERARCHICAL KNOWLEDGE TREE. Every level must hold meaningful, standalone information:
- Leaves contain the most granular context (raw facts, events, decisions)
- {tier_instruction}
- The parent summary should be useful for answering broad overview questions about '{node.name}'.
  Someone reading only this summary should understand the key themes, decisions, and context
  within this domain.

{retrieval_risk_directive}

STRICT RULES:
1. SOURCE MAP KEYS: ONLY use integer IDs from the 'Child Nodes' list above.
   NEVER add sub-IDs, grandchild IDs, or any ID not explicitly listed. Direct children only.
2. SUMMARIES: For each child, produce a concise thematic summary as a FLAT STRING (not a dict).
   - Capture the key concepts, themes, techniques, and knowledge covered by this child.
   - Synthesize — do NOT copy text verbatim. Abstract and compress intelligently.
   - Do NOT include timestamps or dates. Focus purely on WHAT was learned, not WHEN.
   - WARNING: Child input may contain bloated, repetitive text (same facts repeated many times).
     You MUST deduplicate — mention each unique fact once. Never parrot repetitive input.
3. IGNORED IDS: Nodes that are pure noise, duplicates, or carry zero useful information.
   Ignore sparingly — only clearly irrelevant nodes. Use integers only, never strings.

Output JSON only:
{{
  "source_map": {{
    "45": "Covers the Extended Euclidean Algorithm for computing GCD and modular inverses, with applications to RSA key generation.",
    "46": "RSA key generation and decryption correctness using Euler's theorem."
  }},
  "ignored_ids": [47]
}}
"""
                
                response_json = self.extractor.summary_extract(prompt)
            else:
                current_map_text = json.dumps(source_map, indent=2)
                
                prompt = f"""
You are updating the hierarchical knowledge summary for parent topic: '{node.name}'.

Incoming Child Updates (ONLY these integer IDs may be used in your response):
{child_text}

Current Source Map (context only — return ONLY the changes; omit unchanged entries):
{current_map_text}

This is a HIERARCHICAL KNOWLEDGE TREE. Every level must hold meaningful, standalone information.
- {tier_instruction}
- The parent summary should be useful for answering broad overview questions about '{node.name}'.
  Someone reading only this summary should understand the key themes, decisions, and context.

{retrieval_risk_directive}

STRICT RULES:
1. SOURCE MAP KEYS: ONLY use integer IDs from the 'Incoming Child Updates' list.
   NEVER reference sub-IDs, grandchild IDs, or IDs not listed above.
2. SUMMARIES: For each child, produce a concise thematic summary as a FLAT STRING.
   Capture the key concepts, themes, and knowledge. No timestamps or dates.
   Synthesize, don't copy. Deduplicate aggressively.
3. ALL IDs in 'modify', 'remove', and 'add_ignore' must be integers, never strings.

Output JSON patch only (omit unchanged entries entirely):
{{
  "modify": {{ "45": "Updated thematic summary of this child's knowledge domain." }},
  "remove": [48],
  "add_ignore": [49]
}}
"""
                response_json = self.extractor.summary_extract(prompt)

            try:
                data = {}
                if isinstance(response_json, str):
                    clean_json = response_json.replace("```json", "").replace("```", "")
                    s = clean_json.find("{")
                    e = clean_json.rfind("}")
                    if s != -1 and e != -1:
                        clean_json = clean_json[s:e+1]
                    data = json.loads(clean_json)
                else:
                    data = response_json
                
                if not source_map and not ignored_ids:
                    # Cold Start Response Logic
                    source_map = data.get("source_map", {})
                    ignored_ids = set(int(x) for x in data.get("ignored_ids", []) if str(x).isdigit())
                else:
                    # Patch Response Logic
                    mods = data.get("modify", {})
                    removes = data.get("remove", [])
                    new_ignores = data.get("add_ignore", [])
                    
                    for uid, text in mods.items():
                        source_map[str(uid)] = text
                        if int(uid) in ignored_ids:
                            ignored_ids.remove(int(uid))
                    
                    for uid in removes:
                        if str(uid) in source_map:
                            del source_map[str(uid)]
                            ignored_ids.add(int(uid))
                    
                    for uid in new_ignores:
                        ignored_ids.add(int(uid))

                # Check for skipped IDs from the batch and add them to ignored_ids safely
                for update_str in batch:
                    if update_str.startswith("ID "):
                        try:
                            sid_str = update_str.split(" ", 2)[1]
                            sid = int(sid_str)
                            # Check both int and str to be absolutely safe against inconsistent LLM format returns
                            if str(sid) not in source_map and sid not in ignored_ids:
                                ignored_ids.add(sid)
                        except ValueError:
                            pass

            except Exception as e:
                logger.error(f"  [Parent] Error parsing LLM response for '{node.name}': {e}")
                all_success = False
                
        if updates_batches:
            node.summary = json.dumps({
                "source_map": source_map,
                "ignored_ids": list(ignored_ids),
                "updates_count": updates_count + (0 if was_cold_start else 1)
            })
            # Build description from flat source_map strings
            node.description = "\n".join(str(v) for v in source_map.values())


        if all_success:
            node.timestamp = datetime.now()
        node.embedding = None  # Clear so embedding job recalculates
        session.add(node)
        logger.info(f"  [Parent] Saved '{node.name}' (Map Size: {len(source_map)})")
        

    def run(self, user_id="default"):
        session = self.Session()
        max_depth = self.get_max_depth(session)
        logger.info(f"[Summarizer] Max Depth: {max_depth} for user '{user_id}'. Starting Bottom-Up Rollup...")
        
        # 1. Process all leaf nodes first using batched summarization
        all_topics = session.query(Topic).filter(Topic.user_id == user_id).all()
        leaf_nodes = [node for node in all_topics if not node.children]
        self.process_leaves_batched(session, leaf_nodes)

        # 2. Process parent nodes in bottom-up level-by-level order
        for current_level in range(max_depth, -1, -1):
            nodes = session.query(Topic).filter_by(level=current_level, user_id=user_id).all()
            if not nodes: continue
            
            parent_nodes = [node for node in nodes if node.children]
            if not parent_nodes: continue

            logger.info(f"\n--- Processing Level {current_level} ({len(parent_nodes)} parent nodes) ---")

            for node in parent_nodes:
                self.process_parent(session, node, max_depth)
                session.commit()
        
        logger.info("\n[Summarizer] Rollup Complete.")
        session.close()

if __name__ == "__main__":
    summarizer = RecursiveSummarizer()
    summarizer.run()