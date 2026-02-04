from typing import Optional
from Database.embedder import EmbeddingManager
from retrieval.structs import RetrievalConfig, RetrievalResult
from retrieval.context_bridge import ContextBridge
from retrieval.root_search import RootSearch
from retrieval.root_descent import RootDescent
from retrieval.agentic_refiner import AgenticRefiner

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
        self.refiner = AgenticRefiner(self.config)

    def retrieve(self, current_prompt: str, last_msg: str = None, prev_msg: str = None) -> RetrievalResult:
        """
        Execute the "Active Path" pipeline.
        
        Phase 1: Context Bridge (Input -> Query Vector)
        Phase 2: Search (Query Vector -> Root Node)
        Phase 3: Root Descent (Descent from Root Node -> Candidate Sources)
        Phase 4: Agentic Refinement (Candidates -> Selected JSON)
        """
        ctx = self.bridge.process(current_prompt, last_msg, prev_msg)
        root_node = self.search.scan(ctx)
        
        if not root_node:
            return RetrievalResult(
                 context_str="No relevant long-term memory found (Chitchat mode)"
             )
        print(root_node)
        descent_result = self.descent.descend(root_node, ctx)
        print(descent_result)
        refined_output = self.refiner.refine(
            query=ctx.query_text if ctx.query_text else ctx.current_prompt,
            candidates=descent_result.candidates
        )
        return refined_output
