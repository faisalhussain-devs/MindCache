from Database.db_setup import Topic
from Database.db_manager import DatabaseManager
from retrieval.structs import RetrievalContext, RetrievalConfig, RetrievalResult
from retrieval.context_bridge import ContextBridge

class RootDescent:
    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session

    def descend(self, root_node: Topic, ctx: RetrievalContext) -> RetrievalResult:
        """
        Phase 3: Beam Descent.
        Prune the sub-graph of the elected Root Node.
        """
        session = self.Session()
        final_context_parts = []
        sources = []
        debug_trace = []
        
        try:
            # Re-merge root_node into this session
            root = session.query(Topic).get(root_node.id)
            if not root:
                return RetrievalResult("", [], ["Root node not found in Phase 3"])

            debug_trace.append(f"Root Selected: {root.name}")
            final_context_parts.append(f"# Domain: {root.name}")
            
            # Start Recursion
            self._recursive_scan(
                session=session,
                node=root, 
                query_vec=ctx.query_vector, 
                context_parts=final_context_parts, 
                sources=sources, 
                debug_trace=debug_trace,
                current_path=[root.name]
            )

            return RetrievalResult(
                context_str="\n".join(final_context_parts),
                sources=sources,
                debug_log=debug_trace
            )

        finally:
            session.close()

    def _recursive_scan(self, session, node, query_vec, context_parts, sources, debug_trace, current_path):
        children = node.children
        if not children:
            return

        scored_children = []
        for child in children:
            if child.embedding is None: continue
            child_vec = self.db_manager._from_blob(child.embedding)
            score = self.bridge._cosine_similarity(query_vec, child_vec)
            scored_children.append((score, child))
        
        scored_children.sort(key=lambda x: x[0], reverse=True)

        for score, child in scored_children:
            child_path = current_path + [child.name]
            path_str = " > ".join(child_path)

            if score >= self.config.hot_threshold:
                debug_trace.append(f"HOT ({score:.2f}): {child.name}")
                context_parts.append(f"[Current Topic]: {child.name} Path: {path_str} [Summary] {child.summary}")
                sources.append({"id": child.id, "name": child.name, "score": float(score), "type": "topic_hot"})
                self._recursive_scan(session, child, query_vec, context_parts, sources, debug_trace, child_path)
                
            elif score >= self.config.cold_threshold:
                debug_trace.append(f"WARM ({score:.2f}): {child.name}")
                context_parts.append(f"[Current Topic]: {child.name} Path: {path_str}")
                sources.append({"id": child.id, "name": child.name, "score": float(score), "type": "topic_cold"})
                self._recursive_scan(session, child, query_vec, context_parts, sources, debug_trace, child_path)
            else:
                pass
