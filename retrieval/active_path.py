from typing import Optional
from Memory_extract.embedder import EmbeddingManager
from retrieval.structs import RetrievalConfig, RetrievalResult
from retrieval.context_bridge import ContextBridge
from retrieval.root_search import RootSearch
from retrieval.root_descent import RootDescent

class ActivePathRetrieval:
    def __init__(self, config: Optional[RetrievalConfig] = None):
        if config is None:
            config = RetrievalConfig()
        self.config = config
        
        # Initialize Shared Resources
        self.embedder = EmbeddingManager()
        
        # Initialize Phases
        self.bridge = ContextBridge(self.embedder, self.config)
        self.search = RootSearch(self.config, self.bridge)
        self.descent = RootDescent(self.config, self.bridge)

    def retrieve(self, current_prompt: str, last_msg: str = None, prev_msg: str = None) -> RetrievalResult:
        """
        Execute the "Active Path" pipeline.
        
        Phase 1: Context Bridge (Input -> Query Vector)
        Phase 2: Search (Query Vector -> Root Node)
        Phase 3: Root Descent (Descent from Root Node -> Context String)
        """
        # Phase 1
        ctx = self.bridge.process(current_prompt, last_msg, prev_msg)
        
        # Phase 2
        root_node = self.search.scan(ctx)
        
        if not root_node:
             # Fallback: No relevant domain found.
             return RetrievalResult(
                 context_str="No relevant long-term memory found (Chitchat mode).",
                 sources=[],
                 debug_log=["Phase 2: No Root Node selected (Score < Threshold)"]
             )
        
        # Phase 3
        result = self.descent.descend(root_node, ctx)
        
        # Merge debug logs
        result.debug_log = [f"Phase 1: Input processed. Used history: {ctx.history_used}"] + \
                           [f"Phase 2: Selected Root '{root_node.name}'"] + \
                           result.debug_log
                           
        return result
