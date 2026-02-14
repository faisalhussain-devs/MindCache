import json
from retrieval.structs import RetrievalConfig, RefinedSelection, CandidateTopic
from Memory_extract.safe_ai import SafeAI

SYSTEM_PROMPT = """You are the Retrieval Refinement Engine for a memory database.
You receive a user query and a list of candidate topics found by vector + keyword search.

### YOUR JOB
1. Analyze the user query intent.
2. Select the MOST relevant topics from the candidates.
3. For each selected topic, decide the retrieval DEPTH:
   - "summary" → High-level overview is sufficient (e.g., "what's the project about?")
   - "leaf" → Need specific details, code, or decisions (e.g., "what regex fix did I apply?")

### INPUTS
- **Query**: May contain [CURRENT], [PREV], [PREV2] tags. Focus primarily on [CURRENT].
  Use [PREV] context only if it clarifies the current intent.
- **Candidates**: Each has name, path, similarity score, BM25 score, timestamp, and whether it's a leaf node.

### RULES
- Select 1-3 topics max. Less is better.
- Higher sim_score + bm25_score = more relevant.
- If a topic is a leaf node and depth is "leaf", the system will fetch atomic memories.
- If a topic is NOT a leaf (parent), depth "leaf" will fetch its summary (built from child data).

### OUTPUT FORMAT (Strict JSON)
{schema_json}

Do not output markdown or explanations outside the JSON."""

USER_TEMPLATE = """Query: {query}

Candidates:
{candidates}

Select the relevant topics and decide retrieval depth."""


class AgenticRefiner:
    def __init__(self, config: RetrievalConfig):
        self.config = config
        self.ai = SafeAI(model_name=config.model_name)
        
    def refine(self, query: str, candidates: list[CandidateTopic]) -> dict:
        """
        Lean Refiner + Depth Decision.
        Input: query + top-k candidate names/scores (no descriptions).
        Output: selected topics with depth instruction.
        """
        if not candidates:
            return {"reasoning": "No candidates found.", "selected_topics": []}

        # Build lean candidate text (name + scores + timestamp only)
        candidate_lines = []
        for c in candidates:
            line = (
                f"- {c.path} | sim: {c.sim_score} | bm25: {c.bm25_score} "
                f"| time: {c.timestamp} | leaf: {c.is_leaf}"
            )
            candidate_lines.append(line)
        candidates_text = "\n".join(candidate_lines)

        schema = RefinedSelection.model_json_schema()
        sys_prompt = SYSTEM_PROMPT.format(schema_json=json.dumps(schema, indent=2))
        usr_prompt = USER_TEMPLATE.format(query=query, candidates=candidates_text)

        raw_response = self.ai.generate(usr_prompt, system_prompt=sys_prompt)
        if not raw_response:
            return {"reasoning": "Generation failed", "selected_topics": []}
            
        try:
            selection = RefinedSelection.model_validate_json(raw_response)
            return selection.model_dump()
        except Exception as e:
            print(f"[AgenticRefiner] Validation Error: {e}")
            return {"reasoning": f"Validation Error: {str(e)}", "selected_topics": []}
