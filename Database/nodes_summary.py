from sqlalchemy import func
from Database.db_setup import engine, Topic, Memory, TriadBlock
from Memory_extract.summary_extractor import Summary_Extractor
from sqlalchemy.orm import sessionmaker

sum_ext = Summary_Extractor()
def generate_parent_summary(node_name, child_summaries_text):
    """
    Goal: Synthesize multiple reports into a high-level overview.
    """

    sys_prompt = f"""
    You are the Project Lead
    Below are the status reports from your sub-modules.
    Synthesize them into a single coherent status report. Highlight key progress and any cross-module blockers.
    Do NOT list every small detail; focus on the bigger picture.

    SUB-MODULE REPORTS:

    OUTPUT (Executive Synthesis):
    """
    sum_ext.sys_prompt = sys_prompt

    prompt = f"This is the topic name {node_name} and this is the report {child_summaries_text}"
    return sum_ext.summary_extract(prompt=prompt)


# 2. THE SUMMARIZATION ENGINE
class RecursiveSummarizer:
    def __init__(self):
        self.Session = sessionmaker(bind=engine)
        self.session = self.Session()

    def get_max_depth(self):
        result = self.session.query(func.max(Topic.level)).scalar()
        return result or 0

    def get_atomic_timeline(self, topic_id):
        """
        Fetches MESSAGE SUMMARIES (TriadBlock) relevant to this Leaf Node.
        Deduplicates messages (since one message can spawn multiple atomic memories).
        """
        results = (
            self.session.query(TriadBlock)
            .join(Memory, Memory.message_id == TriadBlock.id)
            .filter(Memory.topic_id == topic_id)
            .group_by(TriadBlock.id)  # Group by the Message ID to prevent duplicate entries
            .order_by(TriadBlock.timestamp.asc())
            .all()
        )
        
        if not results: return None
        
        timeline = []
        for triad in results:
            ts = triad.timestamp.strftime('%Y-%m-%d %H:%M')
            content = triad.summary
            if content:
                timeline.append(f"[{ts}] {content}")
            
        return "\n".join(timeline)

    def run(self):
        session = self.Session()
        max_depth = self.get_max_depth()
        print(f"[Summarizer] Max Depth: {max_depth}. Starting Rollup...")

        # LOOP: Bottom-Up (Deepest -> Root)
        for current_level in range(max_depth, -1, -1):
            nodes = session.query(Topic).filter_by(level=current_level).all()
            print(f"\n--- Processing Level {current_level} ({len(nodes)} nodes) ---")

            for node in nodes:
                # CHECK: Is this a Leaf or a Parent?
                is_leaf = not bool(node.children)

                if is_leaf:
                    # LEAF NODE (Atomic Memories)
                    leaf_summary = self.get_atomic_timeline(node.id)
                    node.summary = leaf_summary
                else:
                    # Aggregate the summaries of immediate children
                    child_reports = []
                    for child in node.children:
                        c_sum = child.summary
                        child_reports.append(f"{child.name}: {c_sum}")
                    
                    combined_text = "\n".join(child_reports)
                    
                    if combined_text:
                        print(f"  [Parent] Synthesizing '{node.name}' from {len(node.children)} children...")
                        new_summary = generate_parent_summary(node.name, combined_text)
                        node.summary = new_summary

                # Add to session (staged for commit)
                session.add(node)

            # Commit the whole level at once
            session.commit()

        print("\n[Summarizer] Complete. Root Node is updated.")

if __name__ == "__main__":
    job = RecursiveSummarizer()
    job.run()