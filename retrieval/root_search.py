from typing import Optional
from Database.db_setup import Topic
from Database.db_manager import DatabaseManager
from retrieval.structs import RetrievalContext, RetrievalConfig
from retrieval.context_bridge import ContextBridge

class RootSearch:
    def __init__(self,config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge # used for vector helpers
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session

    def scan(self, ctx: RetrievalContext) -> Optional[Topic]:
        """
        Phase 2: The Sniper Root Scan.
        Identify the single best broad domain (Root Node).
        """
        session = self.Session()
        try:
            roots = session.query(Topic).filter_by(level=0).all()
            
            if not roots:
                return None
            best_score = -1.0
            best_node = None
            query_vec = ctx.query_vector

            for root in roots:
                if root.embedding is None:
                    continue
                
                root_vec = self.db_manager._from_blob(root.embedding)
                score = self.bridge._cosine_similarity(query_vec, root_vec)
                
                print(f"[DEBUG] Root Search: {root.name} Score: {score}")
                
                if score > best_score:
                    best_score = score
                    best_node = root

            if best_score >= self.config.root_selection_threshold:
                session.expunge(best_node) # Detach it so we can use it after close
                return best_node
            return None
        finally:
            session.close()
