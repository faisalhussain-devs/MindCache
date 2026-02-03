import json
from datetime import datetime
from sqlalchemy import func
from Database.db_setup import engine, Topic, Memory, TriadBlock
from Memory_extract.summary_extractor import Summary_Extractor
from sqlalchemy.orm import sessionmaker

MAJOR_UPDATE_THRESHOLD = 50

class RecursiveSummarizer:
    def __init__(self):
        self.Session = sessionmaker(bind=engine)
        self.extractor = Summary_Extractor()

    def get_max_depth(self, session):
        result = session.query(func.max(Topic.level)).scalar()
        return result or 0

    def get_leaf_summary(self, session, topic_id, min_timestamp=0):
        """
        Fetches chronologically ordered summaries of message blocks (TriadBlock) 
        assigned to this topic.
        """
        query = (
            session.query(TriadBlock)
            .join(Memory)
            .filter(Memory.topic_id == topic_id)
        )

        if min_timestamp > 0:
            dt = datetime.fromtimestamp(min_timestamp)
            query = query.filter(TriadBlock.timestamp > dt)

        query = query.group_by(TriadBlock.id).order_by(TriadBlock.timestamp.asc())
        results = query.all()
        
        if not results: return ""

        timeline = []
        for triad in results:
            ts = triad.timestamp.strftime('%Y-%m-%d %H:%M')
            content = triad.summary
            if content:
                timeline.append(f"[{ts}] {content}")
            
        return "\n".join(timeline)

    def process_leaf(self, session, node):
        """
        Leaf Node: 
        1. Summary = Concatenation of TriadBlock Summaries (Log).
        2. Description = 
           - IF Empty: Generate from scratch (All Summaries).
           - IF Exists: Incremental Update (Old Desc + New Summaries).
        """
        if node.summary and node.description:
            last_ts = node.timestamp.timestamp()
            new_content = self.get_leaf_summary(node.id, min_timestamp=last_ts)
            if not new_content: return
            node.summary += " " + new_content
            print(f"  [Leaf] Updating Description for '{node.name}'")

            desc_prompt = f"""
            Update the following description with new information.
            Current Description: {node.description}   
            New Information: {new_content[:3000]}
            Output ONLY the updated concise description suitable for retrieval.
            """
        else:
            full_content = self.get_leaf_summary(node.id, min_timestamp=0)
            if not full_content: return
            node.summary = full_content
            print(f" [Leaf] Init Description for '{node.name}'")\

            desc_prompt = f"""
            Create a description for the following information.
            New Information: {full_content}
            Output ONLY the concise description suitable for retrieval.
            """

            new_desc = self.extractor.summary_extract(desc_prompt)
            if new_desc: node.description = new_desc
            
        node.timestamp = datetime.now()
        session.add(node)

    def process_parent(self, session, node):
        """
        Parent Node (Source-Map Architecture):
        - Summary: JSON Blob { "source_map": { "id": "Contextual Summary" }, "ignored_ids": [id...] }
        - Logic: 
            1. Maintain a map of "Important Child Nodes".
            2. On update, check if Child is in Source Map or Ignored.
            3. Update the Map Entry or move lists.
            4. Description is generated from the Source Map.
        """
        children = node.children
        if not children: return
        try:
            if node.summary and node.summary.startswith("{"):
                state = json.loads(node.summary)
            else:
                state = {}
        except:
            state = {}

        source_map = state.get("source_map", {})
        ignored_ids = set(state.get("ignored_ids", []))
        
        # 2. Identify Changes
        updates = []  
        
        for child in children:
            child_ts = child.timestamp or datetime.min
            node_ts = node.timestamp or datetime.min
            
            if child_ts > node_ts:
                status = "EXISTING_SOURCE" if str(child.id) in source_map else ("IGNORED" if child.id in ignored_ids else "NEW")
                if status == "IGNORED" and len(child.summary or "") < 50:
                    continue
                    
                updates.append({
                    "id": child.id,
                    "name": child.name,
                    "summary": child.summary or "",
                    "status": status
                })

        if not updates and source_map:
            return 

        if not source_map and not ignored_ids:
            print(f"  [Parent] Cold Start Source Mapping for '{node.name}'")
            child_text = "\n".join([f"ID {c.id} ({c.name}): {c.summary}" for c in children])
            
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

        else:
            print(f"  [Parent] Patching Source Map for '{node.name}'")
            current_map_text = json.dumps(source_map, indent=2)
            updates_text = "\n".join([f"[{u['status']}] ID {u['id']} ({u['name']}): {u['content']}" for u in updates])
            
            prompt = f"""
            Update the Parent Source Map based on changes.
            RETURN ONLY THE CHANGES (Diffs). Do not output unchanged items.

            Current Map:
            {current_map_text}
            
            Current Description:
            {node.description}
            
            Incoming Updates:
            {updates_text[:5000]}
            
            Instructions:
            1. 'modify': Dictionary of ID -> New Summary (for EXISTING or promoted NEW/IGNORED).
            2. 'remove': List of IDs to remove from map (if no longer relevant).
            3. 'add_ignore': List of IDs to add to ignore list.
            4. 'description': The updated high-level description.
            
            Output JSON (The Patch):
            {{
              "modify": {{ "45": "Updated summary text..." }},
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
                source_map = data.get("source_map", {})
                ignored_ids = set(data.get("ignored_ids", []))
            else:
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

if __name__ == "__main__":
    job = RecursiveSummarizer()
    job.run()
