from typing import Optional
from Memory_extract.embedder import EmbeddingManager
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
        # Phase 1
        ctx = self.bridge.process(current_prompt, last_msg, prev_msg)
        
        # Phase 2
        root_node = self.search.scan(ctx)
        
        if not root_node:
             return RetrievalResult(
                 context_str="No relevant long-term memory found (Chitchat mode).",
                 sources=[],
                 debug_log=["Phase 2: No Root Node selected (Score < Threshold)"]
             )
        
        # Phase 3
        # result.context_str here is the "Database View" (The big tree)
        # result.sources contains the list of items
        descent_result = self.descent.descend(root_node, ctx)
        
        # Phase 4: Agentic Refinement
        # We pass the "Database View" (sources list) to the LLM
        refined_output = self.refiner.refine(
            query=ctx.query_text if ctx.query_text else ctx.current_prompt,
            candidates=descent_result.sources
        )
        
        # Update Result
        descent_result.refined_json = refined_output
        descent_result.debug_log = [f"Phase 1: Input processed. Used history: {ctx.history_used}"] + \
                           [f"Phase 2: Selected Root '{root_node.name}'"] + \
                           descent_result.debug_log + \
                           ["Phase 4: Agentic Refinement Complete"]
        
        # OPTIONAL: Replace context_str with the JSON if the user wants purely the JSON output?
        # User said: "which output a json about retreival ... the llm takes the user query (complete ) with the database view outputed"
        # I will Append the JSON analysis to the context string for visibility.
        import json
        descent_result.context_str += f"\n\n[AGENTIC REFINEMENT]\n{json.dumps(refined_output, indent=2)}"
                           
        return descent_result
