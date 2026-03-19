from typing import Optional
from Database.db_setup import Topic
from Database.db_manager import DatabaseManager
from retrieval.structs import RetrievalContext, RetrievalConfig
from retrieval.context_bridge import ContextBridge
import numpy as np

class RootSearch:
    def __init__(self,config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge # used for vector helpers
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session

    def scan(self, ctx: RetrievalContext) -> list[Topic]:
        """
        Phase 2: The Broad Root Scan.
        Identify the Top-K best broad domains (Root Nodes).
        """
        session = self.Session()
        try:
            roots = session.query(Topic).filter_by(level=0).all()
            
            if not roots:
                return []

            query_vec = ctx.query_vector
            scored_roots = []
            valid_roots = []
            root_vecs = []

            for root in roots:
                if root.embedding is not None:
                    valid_roots.append(root)
                    root_vecs.append(self.db_manager._from_blob(root.embedding))

            if valid_roots:
                root_matrix = np.array(root_vecs)
                # Compute similarities in a vectorized batch (assuming vectors are normalized)
                scores = np.dot(root_matrix, query_vec)

                # Find indices of scores that meet the threshold
                valid_indices = np.where(scores >= self.config.root_selection_threshold)[0]
                
                if len(valid_indices) > 0:
                    # Filter scores and get corresponding indices relative to valid_roots
                    filtered_scores = scores[valid_indices]
                    
                    # Sort the filtered scores in descending order and limit to Top K
                    top_k_idx = np.argsort(filtered_scores)[::-1][:self.config.top_k_roots]
                    
                    for idx in top_k_idx:
                        original_idx = valid_indices[idx]
                        val = float(filtered_scores[idx])
                        print(f"[DEBUG] Root Search: {valid_roots[original_idx].name} Score: {val:.4f}")
                        scored_roots.append((val, valid_roots[original_idx]))

            top_roots = [item[1] for item in scored_roots]
            
            for root in top_roots:
                session.expunge(root) # Detach so we can use them after close
                
            return top_roots
        finally:
            session.close()
