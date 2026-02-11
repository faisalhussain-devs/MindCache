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

    def get_max_depth(self, session):
        result = session.query(func.max(Topic.level)).scalar()
        return result or 0

    def get_leaf_summary(self, session, topic_id, min_timestamp=0):
        """
        Fetches memories for this topic across all 4 memory types.
        Returns a dict: { "YYYY-MM-DD HH:MM": { "global_id": "[Type] content", ... }, ... }
        Ordered chronologically by insertion order.
        """
        memory_models = [
            (EpisodicMemory, 'Episodic'),
            (UserMemory, 'User'),
            (KnowledgeMemory, 'Knowledge'),
            (DecisionMemory, 'Decision')
        ]

        min_dt = datetime.fromtimestamp(min_timestamp) if min_timestamp > 0 else datetime.min

        raw_entries = []
        for Model, type_name in memory_models:
            query = session.query(Model).filter(Model.topic_id == topic_id)
            if min_timestamp > 0:
                query = query.filter(Model.timestamp > min_dt)
            
            for mem in query.all():
                if mem.content:
                    raw_entries.append((
                        mem.timestamp if mem.timestamp else datetime.min,
                        mem.id,  # Global registry ID
                        type_name,
                        mem.content
                    ))

        if not raw_entries:
            return {}

        raw_entries.sort(key=lambda x: x[0])

        summary_dict = {}
        for ts, mem_id, mem_type, content in raw_entries:
            ts_key = ts.strftime('%Y-%m-%d %H:%M')
            if ts_key not in summary_dict:
                summary_dict[ts_key] = {}
            summary_dict[ts_key][mem_id] = f"[{mem_type}] {content}"

        return summary_dict

    def _format_leaf_summary(self, summary_dict, latest_first=True):
        """
        Converts the leaf summary dict into a readable string for LLM prompts.
        If latest_first=True, reverses the timestamp order.
        """
        if not summary_dict:
            return ""
        
        keys = list(summary_dict.keys())
        if latest_first:
            keys = reversed(keys)

        lines = []
        for ts_key in keys:
            memories = summary_dict[ts_key]
            for mem_id, content in memories.items():
                lines.append(f"[{ts_key}] (#{mem_id}) {content}")
        
        return "\n".join(lines)

    def process_leaf(self, session, node):
        """
        Leaf Node: 
        1. Summary = JSON dict { timestamp: { global_id: "[Type] content" } }
        2. Description = 
           - IF Empty: Generate from scratch (from all memories).
           - IF Exists: Incremental Update (Old Desc + New memories).
        """
        # Load existing summary state
        existing_summary = {}
        try:
            if node.summary and node.summary.startswith("{"):
                existing_summary = json.loads(node.summary)
        except:
            existing_summary = {}

        last_ts = 0
        if node.timestamp:
            last_ts = node.timestamp.timestamp()
        new_memories = self.get_leaf_summary(session, node.id, min_timestamp=last_ts)

        for ts_key, mem_dict in new_memories.items():
            if ts_key not in existing_summary:
                existing_summary[ts_key] = {}
            existing_summary[ts_key].update(mem_dict)
        
        node.summary = json.dumps(existing_summary)

        full_text = self._format_leaf_summary(existing_summary)
        new_text = self._format_leaf_summary(new_memories)

        if not node.description:
            print(f"  [Leaf] Init Description for '{node.name}'")
            if full_text:
                desc_prompt = f"""
                Create a description for the following information.
                Information: {full_text[:5000]}
                Output ONLY the concise description suitable for retrieval.
                """
                new_desc = self.extractor.summary_extract(desc_prompt)
                if new_desc: node.description = new_desc
        
        elif new_text:
            print(f"  [Leaf] Updating Description for '{node.name}'")
            desc_prompt = f"""
            Update the following description with new information.
            Current Description: {node.description}   
            New Information: {new_text[:3000]}
            Output ONLY the updated concise description suitable for retrieval.
            """
            new_desc = self.extractor.summary_extract(desc_prompt)
            if new_desc: node.description = new_desc
            
        node.timestamp = datetime.now()
        session.add(node)

    def process_parent(self, session, node):
        """
        Parent Node (Source-Map Architecture):
        - Summary: JSON Blob { "source_map": { "id": "Contextual Summary" }, "ignored_ids": [id...] }
        """
        children = node.children
        sorted_children = sorted(children, key=lambda x: x.timestamp or datetime.min, reverse=True)
        if not sorted_children: return

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
        
        for child in sorted_children:
            child_ts = child.timestamp or datetime.min
            node_ts = node.timestamp or datetime.min
            
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
              "description": "A concise description..."
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
            # print(f"Raw Response: {response_json}") 

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

if __name__ == "__main__":
    job = RecursiveSummarizer()
    job.run()
