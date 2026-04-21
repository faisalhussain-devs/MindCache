import json
from datetime import datetime
from sqlalchemy import func
from Database.db_setup import engine, Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Memory_extract.summary_extractor import Summary_Extractor
from sqlalchemy.orm import sessionmaker

MAJOR_UPDATE_THRESHOLD = 60

class RecursiveSummarizer:
    def __init__(self):
        self.Session = sessionmaker(bind=engine)
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
        Leaf Node: 
        1. Summary = JSON { "memories": {...}, "decisions": {...} }
        """
        # Load existing summary state

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
                        min_timestamp = datetime.strptime(max_date_str, '%Y-%m-%d %H:%M').timestamp() + 60
                else:
                    raise ValueError("Error: Invalid summary format")
        except Exception as e:
            print(f"Error: {e}")
            existing_summary = {"memories": {}, "decisions": {}}

        new_data = self.get_leaf_summary(session, node.id, min_timestamp=min_timestamp)
        if not new_data:
            return
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
            node.summary = json.dumps(existing_summary)
            node.description = self._build_search_text(existing_summary)
            node.timestamp = datetime.now()
            # Update mem_start / mem_end from the actual memory timestamp keys
            all_keys = (list(existing_summary.get("memories", {}).keys()) +
                        list(existing_summary.get("decisions", {}).keys()))
            if all_keys:
                fmt = "%Y-%m-%d %H:%M"
                parsed = [datetime.strptime(k, fmt) for k in all_keys]
                node.mem_start = min(parsed)
                node.mem_end   = max(parsed)
            session.add(node)
        
    def process_parent(self, session, node):
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
            node.embedding = child.embedding
            node.description = child.description
            session.add(node)
            return

        sorted_children = sorted(children, key=lambda x: x.timestamp or datetime.min, reverse=True)
        try:
            if node.summary and node.summary.startswith("{"):
                state = json.loads(node.summary)
            else:
                state = {}
        except:
            state = {}

        source_map = state.get("source_map", {})
        ignored_ids = set(int(x) for x in state.get("ignored_ids", []) if str(x).isdigit())
        
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
                    try:
                        leaf_data = json.loads(child.summary) if child.summary else {}
                        child_content = self._format_leaf_summary(leaf_data)
                    except:
                        child_content = str(child.summary)
                else:
                    child_content = f"[{child.timestamp}] {child.summary}"

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
        for batch in updates_batches:
            child_text = "\n".join(batch)
            if not source_map and not ignored_ids:
                prompt = f"""
                Analyze the following Child Nodes for the Topic '{node.name}'.
                Create a 'Source Map' of the most important nodes that contribute to the main theme.
                Mark irrelevant/noise nodes as ignored.

                Child Nodes:
                {child_text}
                
                Instructions:
                1. 'source_map': Dictionary mapping Child ID to a 1-sentence contextual summary of why it matters.
                2. 'ignored_ids': List of Child IDs that are too low-level or irrelevant for a high-level summary.
                        
                Output JSON:
                {{
                  "source_map": {{ "45": "Recursion error fixed in main loop.", ... }},
                  "ignored_ids": [46, 47],
                }}
                """
                
                response_json = self.extractor.summary_extract(prompt)
            else:
                current_map_text = json.dumps(source_map, indent=2)
                
                prompt = f"""
                Update the Parent Source Map based on changes.
                RETURN ONLY THE CHANGES (Diffs). Do not output unchanged items.

                Current Map:
                {current_map_text}
                
                Incoming Updates:
                {child_text}
                
                Instructions:
                1. 'modify': Dictionary of ID -> New Summary.
                2. 'remove': List of IDs to remove.
                3. 'add_ignore': List of IDs to add to ignore list.
                
                Output JSON (The Patch):
                {{
                  "modify": {{ ... }},
                  "remove": [],
                  "add_ignore": [],
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
                            if str(sid) not in source_map and sid not in ignored_ids and str(sid) not in ignored_ids:
                                ignored_ids.add(sid)
                        except ValueError:
                            pass

            except Exception as e:
                print(f"  [Parent] Error parsing LLM response for '{node.name}': {e}")
                all_success = False
                
        if updates_batches:
            node.summary = json.dumps({
                "source_map": source_map,
                "ignored_ids": list(ignored_ids)
            })
            node.description = " ".join(source_map.values())
            # Propagate mem_start / mem_end from children columns
            child_starts = [c.mem_start for c in children if c.mem_start]
            child_ends   = [c.mem_end   for c in children if c.mem_end]
            if child_starts:
                node.mem_start = min(child_starts)
            if child_ends:
                node.mem_end = max(child_ends)


        if all_success:
            node.timestamp = datetime.now()
        session.add(node)
        print(f"  [Parent] Saved '{node.name}' (Map Size: {len(source_map)})")
        

    def run(self):
        session = self.Session()
        max_depth = self.get_max_depth(session)
        print(f"[Summarizer] Max Depth: {max_depth}. Starting Bottom-Up Rollup...")
        for current_level in range(max_depth, -1, -1):
            nodes = session.query(Topic).filter_by(level=current_level).all()
            if not nodes: continue
            
            print(f"\n--- Processing Level {current_level} ({len(nodes)} nodes) ---")

            for node in nodes:
                is_leaf = not bool(node.children)
                if is_leaf:
                    self.process_leaf(session, node)
                else:
                    self.process_parent(session, node)
                session.commit()
        
        print("\n[Summarizer] Rollup Complete.")
        session.close()

