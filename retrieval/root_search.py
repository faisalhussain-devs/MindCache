from Database.db_manager import DatabaseManager
from Database.db_setup import Topic
from retrieval.context_bridge import ContextBridge
from retrieval.node_selector import AdaptiveNodeSelector
from retrieval.structs import RetrievalConfig, RetrievalContext

MAX_SELECTED_ROOTS = 6

ROOT_SELECTION_PROMPT = """
You are a routing agent for a hierarchical knowledge system.
Input:
- User query
- List of root nodes

Task:
Select 3-6 most relevant roots where the answer is likely to exist.

Rules:
1. Normalize query:
   - Remove fluff (for example: "remind me", "I was wondering")
   - Extract:
     - Core intent (recall / explain / plan / recommend / compare)
     - Domain (travel, food, business, tech, etc.)
     - Key entities (places, objects, systems, roles)

2. Expand understanding:
   - Do not rely only on surface keywords
   - Interpret the query in broader semantic terms
   - Identify the environment or system the query belongs to
     Examples:
       - "factory" -> business / industry
       - "human development" -> business / organization / HR
       - "restaurant menu" -> food / hospitality

3. Root selection strategy:
   - Select roots based on where structured knowledge would exist
   - Prefer roots that contain the contextual system, not just keywords
   - Always return 3-6 roots when enough good options exist
   - If unsure, include adjacent or parent domains
   - Favor coverage over precision when ambiguity exists

4. Constraints:
   - Max roots <= {max_selected}
   - Avoid redundant roots
   - Do not overfit to narrow interpretations
   - Output the exact root names from the provided list only

5. Multi-hop awareness:
   - If the query spans multiple concepts, include roots covering each step
   - Example: "startup hiring engineers in Germany" -> business + jobs + geography
"""


class RootSearch:
    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session
        self.selector = AdaptiveNodeSelector()

    def scan(self, ctx: RetrievalContext, top_k: int = MAX_SELECTED_ROOTS) -> list[Topic]:
        """
        Phase 2: The broad root scan.
        Use the LLM only when the root fan-out is large; otherwise rank roots by vectors.
        If LLM routing fails, fall back to vectors.
        """
        session = self.Session()
        try:
            roots = (
                session.query(Topic)
                .filter(Topic.parent_id.is_(None))
                .order_by(Topic.name.asc())
                .all()
            )
            if not roots:
                return []

            max_selected = min(top_k, MAX_SELECTED_ROOTS, self.config.llm_max_selected)
            selection = self.selector.select_nodes(
                query_text=(ctx.query_text or ctx.current_prompt or "").strip(),
                nodes=roots,
                max_selected=max_selected,
                llm_trigger_count=self.config.llm_choice_threshold,
                threshold=self.config.root_threshold,
                vector_score=lambda root: self._root_vector_score(ctx, root),
                system_prompt=ROOT_SELECTION_PROMPT.format(max_selected=max_selected),
                user_prompt_builder=self._build_user_prompt,
                vector_limit=max_selected,
            )

            selected_roots = selection.nodes
            for root in selected_roots:
                session.expunge(root)
            return selected_roots
        finally:
            session.close()

    def _build_user_prompt(self, query_text: str, roots: list[Topic]) -> str:
        root_lines = "\n".join(root.name for root in roots if root.name)
        return (
            f"Query context:\n{query_text}\n"
            f"Root nodes:\n{root_lines}\n"
        )

    def _root_vector_score(self, ctx: RetrievalContext, root: Topic) -> float | None:
        if ctx.query_vector is None or root.embedding is None:
            return None

        root_vec = self.db_manager._from_blob(root.embedding)
        return self.bridge._cosine_similarity(ctx.query_vector, root_vec)
