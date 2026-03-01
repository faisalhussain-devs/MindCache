from numba.core.ir import Print
import json
from datetime import datetime
from sqlalchemy import func
from Database.db_setup import engine, Topic, UserMemory, EpisodicMemory, KnowledgeMemory, DecisionMemory
from Database.db_manager import DatabaseManager
from Memory_extract.summary_extractor import Summary_Extractor
from sqlalchemy.orm import sessionmaker

MAJOR_UPDATE_THRESHOLD = 60

class RecursiveSummarizer:
    def __init__(self):
        self.Session = sessionmaker(bind=engine)
        self.extractor = Summary_Extractor()

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

        min_dt = datetime.fromtimestamp(min_timestamp) if min_timestamp > 0 else datetime.min

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

        # --- Decision memories (ordered by last_validated_at) ---
        dec_query = session.query(DecisionMemory).filter(
            DecisionMemory.topic_id == topic_id
        ).order_by(DecisionMemory.last_validated_at.desc())
        if min_timestamp > 0:
            dec_query = dec_query.filter(DecisionMemory.last_validated_at > min_dt)

        decisions_dict = {}
        for dec in dec_query.all():
            if not dec.content:
                continue
            # Use last_validated_at as the timestamp key
            ts = dec.last_validated_at or dec.timestamp or datetime.min
            ts_key = ts.strftime('%Y-%m-%d %H:%M')
            if ts_key not in decisions_dict:
                decisions_dict[ts_key] = {}
            
            ctx = f" | Context: {dec.context}" if dec.context else ""
            decisions_dict[ts_key][dec.id] = f"[Decision:{dec.status}] {dec.content}{ctx}"

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
                lines.append("\n== DECISIONS ==")
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
        2. Description = generated/updated from all memories + decisions w/ status+context
        """
        # Load existing summary state
        existing_summary = {"memories": {}, "decisions": {}}
        try:
            if node.summary and node.summary.startswith("{"):
                parsed = json.loads(node.summary)
                if "memories" in parsed or "decisions" in parsed:
                    existing_summary = parsed
                else:
                    raise ValueError("Error: Invalid summary format")
        except Exception as e:
            print(f"Error: {e}")
            existing_summary = {"memories": {}, "decisions": {}}

        new_data = self.get_leaf_summary(session, node.id, min_timestamp=0)

        if not new_data:
            return

        new_memories = new_data.get("memories", {})
        new_decisions = new_data.get("decisions", {})

        has_new = False
        delta_memories = {}
        delta_decisions = {}

        # Merge memories and detect exactly what is new (ID-based instead of time-based)
        if new_memories:
            for ts_key, mem_dict in new_memories.items():
                if ts_key not in existing_summary["memories"]:
                    existing_summary["memories"][ts_key] = {}
                for mid, content in mem_dict.items():
                    mid_str = str(mid)
                    if mid_str not in existing_summary["memories"][ts_key]:
                        existing_summary["memories"][ts_key][mid_str] = content
                        if ts_key not in delta_memories: delta_memories[ts_key] = {}
                        delta_memories[ts_key][mid_str] = content
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
                        if ts_key not in delta_decisions: delta_decisions[ts_key] = {}
                        delta_decisions[ts_key][did_str] = content
                        has_new = True
        
        node.summary = json.dumps(existing_summary)

        new_text = self._format_leaf_summary({"memories": delta_memories, "decisions": delta_decisions}) if has_new else ""

        if not node.description:
            print(f"  [Leaf] Init Description for '{node.name}'")
            desc_prompt = f"""
            Create a description for the following information.
            Note: Decisions include their current status and reasoning context.
            Information: {new_text[:5000]}
            Output ONLY the concise description suitable for retrieval.
            """
            new_desc = self.extractor.summary_extract(desc_prompt)
            if new_desc: node.description = new_desc
        
        elif new_text:
            print(f"  [Leaf] Updating Description for '{node.name}'")
            desc_prompt = f"""
            Update the following description with new information.
            Note: Decisions include their status (active/superseded/etc.) and context reasoning.
            Current Description: {node.description}   
            New Information: {new_text[:3000]}
            Output ONLY the updated concise description suitable for retrieval.
            """
            new_desc = self.extractor.summary_extract(desc_prompt)
            if new_desc: node.description = new_desc
        if has_new:
            node.timestamp = datetime.now()
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
            node.description = child.description
            node.timestamp = child.timestamp
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
        ignored_ids = set(state.get("ignored_ids", []))
        
        updates = []  
        node_ts = node.timestamp or datetime.min

        for child in sorted_children:
            child_ts = child.timestamp or datetime.min
            if child_ts > node_ts:
                status = "EXISTING_SOURCE" if str(child.id) in source_map else ("IGNORED" if child.id in ignored_ids else "NEW")
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
                    
                updates.append(
                    f"ID {child.id} ({child.name}): {child_content}"
                )
        child_text = "\n".join(updates)

        if not updates and source_map:
            return 

        if not source_map and not ignored_ids:
            print(f"  [Parent] Cold Start Source Mapping for '{node.name}'")
            
            prompt = f"""
            Analyze the following Child Nodes for the Topic '{node.name}'.
            Create a 'Source Map' of the most important nodes that contribute to the main theme.
            Mark irrelevant/noise nodes as ignored and Generate a concise, keyword-rich description too.

            Child Nodes:
            {child_text[:15000]}
            
            Instructions:
            1. 'source_map': Dictionary mapping Child ID to a 1-sentence contextual summary of why it matters.
            2. 'ignored_ids': List of Child IDs that are too low-level or irrelevant for a high-level summary.
            3. 'description': A concise, keyword-rich description for Topic '{node.name}' based on these key points.
            
            Output JSON:
            {{
              "source_map": {{ "45": "Recursion error fixed in main loop.", ... }},
              "ignored_ids": [46, 47],
              "description": "A concise, keyword-rich description for Topic '{node.name}' based on these key points."
            }}
            """
            
            response_json = self.extractor.summary_extract(prompt)

        else:
            print(f"  [Parent] Patching Source Map for '{node.name}'")
            current_map_text = json.dumps(source_map, indent=2)
            
            prompt = f"""
            Update the Parent Source Map based on changes.
            RETURN ONLY THE CHANGES (Diffs). Do not output unchanged items.

            Current Map:
            {current_map_text}
            
            Current Description:
            {node.description}
            
            Incoming Updates:
            {child_text[:5000]}
            
            Instructions:
            1. 'modify': Dictionary of ID -> New Summary.
            2. 'remove': List of IDs to remove.
            3. 'add_ignore': List of IDs to add to ignore list.
            4. 'description': The updated high-level description.
            
            Output JSON (The Patch):
            {{
              "modify": {{ ... }},
              "remove": [],
              "add_ignore": [],
              "description": "Updated description..."
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
                ignored_ids = set(data.get("ignored_ids", []))
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

            new_desc = data.get("description")
            if new_desc:
                node.description = new_desc
            
            node.summary = json.dumps({
                "source_map": source_map,
                "ignored_ids": list(ignored_ids)
            })
            
            node.timestamp = datetime.now()
            session.add(node)
            print(f"  [Parent] Saved '{node.name}' (Map Size: {len(source_map)})")
            
        except Exception as e:
            print(f"  [Parent] Error parsing LLM response for '{node.name}': {e}")

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

def main():
    job = DatabaseManager()
    job.run_decision_state_analyzer()
    job = RecursiveSummarizer()
    job.run()

main()

