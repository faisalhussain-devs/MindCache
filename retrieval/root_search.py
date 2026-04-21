import json
from Database.db_manager import DatabaseManager
from Database.db_setup import Topic
from retrieval.context_bridge import ContextBridge
from retrieval.structs import RetrievalConfig, RetrievalContext, QueryIntelligence
from Memory_extract.safe_ai import SafeAI

QUERY_INTELLIGENCE_PROMPT = """You are a Query Intelligence agent for a personal knowledge graph.

You receive: a user query and a list of root-level knowledge domains.

Your job (in a single JSON response):
1. CLASSIFY the query intent:
   - RECALL → specific fact, decision, date, name ("What did I decide?", "Which library?")
   - EXPLAIN → how/why something works ("How does X work?")
   - COMPARE → differences between options ("X vs Y")
   - PLAN → future actions, todos ("What should I do next?")
   - OVERVIEW → broad summary ("Tell me about X", "What do I know about Y?")

2. DECOMPOSE the query into focused sub-queries.
   - DEFAULT: Output a single sub-query.
   - MULTIPLE: Only if the user query spans multiple distinct domains or has independent parts (e.g. comparing ML vs web dev).

3. For EACH sub-query:
   - SELECT 1-4 root domains from the provided list where the answer likely exists. Output exact names.
   - EXPAND the sub-query into a rich search string (keywords, synonyms, related terms). Keep under 100 words.

Example format:
{{
  "intent": "COMPARE",
  "queries": [
    {{
      "expanded_query": "attention mechanism NLP transformer Vaswani 2017",
      "selected_roots": ["Computer Science"]
    }},
    {{
      "expanded_query": "react frontend framework state management UI components",
      "selected_roots": ["Web Development"]
    }}
  ]
}}
"""
QUERY_INTELLIGENCE_USER = """Query: {query}

Available root domains:
{roots}

Respond with JSON only."""


class RootSearch:
    """Phase 1.5: Query Intelligence.
    
    Single LLM call that analyzes the user query and returns a list of sub-queries.
    Each sub-query specifies its own expanded query string and relevant root nodes.
    """
    
    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session
        self.ai = SafeAI()

    def scan(self, ctx: RetrievalContext) -> list[dict]:
        """
        Phase 2: Query Intelligence.
        
        Returns a list of sub_query dicts:
            [ {"text": "expanded...", "roots": [Topic, Topic]}, ... ]
        """
        session = self.Session()
        sub_queries = []
        try:
            roots = (
                session.query(Topic)
                .filter(Topic.parent_id.is_(None))
                .order_by(Topic.name.asc())
                .all()
            )
            if not roots:
                return []

            # Build root list with descriptions for LLM
            root_lines = []
            root_lookup: dict[str, Topic] = {}
            for root in roots:
                if not root.name:
                    continue
                desc = (root.description or "").strip()
                desc_short = f": {desc[:60]}" if desc else ""
                root_lines.append(f"- {root.name}{desc_short}")
                root_lookup[root.name.lower()] = root

            roots_text = "\n".join(root_lines)
            query_text = (ctx.query_text or ctx.current_prompt or "").strip()

            user_prompt = QUERY_INTELLIGENCE_USER.format(
                query=query_text,
                roots=roots_text,
            )
            
            raw_response = self.ai.generate(
                user_prompt,
                system_prompt=QUERY_INTELLIGENCE_PROMPT,
                json_schema=QueryIntelligence.model_json_schema(),
                retrieval=True,
            )

            if raw_response:
                try:
                    parsed = QueryIntelligence.model_validate_json(
                        self._extract_json(raw_response)
                    )
                    
                    print(f"[QueryIntelligence] Intent: {parsed.intent}")
                    
                    for sq in parsed.queries:
                        selected_topics = []
                        for name in sq.selected_roots:
                            topic = root_lookup.get(name.strip().lower())
                            if topic:
                                session.expunge(topic)
                                selected_topics.append(topic)
                                
                        if selected_topics:
                            expanded = sq.expanded_query or query_text
                            print(f"[QueryIntelligence] Sub-Query: {expanded}... Roots: {[t.name for t in selected_topics]}")
                            sub_queries.append({
                                "text": expanded,
                                "roots": selected_topics
                            })
                            
                except Exception as e:
                    print(f"[QueryIntelligence] Parse error: {e}")

            # Fallback if LLM fails or no sub-queries generated
            if not sub_queries:
                print("[QueryIntelligence] LLM failed/empty, falling back to top vectors")
                fb_roots = self._vector_fallback(roots, ctx, session, top_k=4)
                if fb_roots:
                    sub_queries.append({
                        "text": query_text,
                        "roots": fb_roots
                    })

            return sub_queries

        finally:
            session.close()

    def _vector_fallback(
        self, roots: list[Topic], ctx: RetrievalContext, session, top_k: int
    ) -> list[Topic]:
        """Fallback: rank roots by vector similarity when LLM fails."""
        scored = []
        for root in roots:
            score = self._root_vector_score(ctx, root)
            if score is not None:
                scored.append((score, root))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [root for _, root in scored[:top_k]]
        for root in selected:
            if root in session:
                session.expunge(root)
        return selected

    def _root_vector_score(self, ctx: RetrievalContext, root: Topic) -> float | None:
        if ctx.query_vector is None or root.embedding is None:
            return None
        root_vec = self.db_manager._from_blob(root.embedding)
        return self.bridge._cosine_similarity(ctx.query_vector, root_vec)

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]
        return text
