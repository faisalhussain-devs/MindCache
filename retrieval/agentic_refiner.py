from retrieval.structs import RetrievalConfig, RefinedSelection, CandidateTopic
from Memory_extract.safe_ai import SafeAI

SYSTEM_PROMPT = """You are the **Retrieval Refinement Engine** for MindCache, a personal memory database.

You receive a user query and a ranked list of candidate topics found by the retrieval pipeline (vector similarity + BM25 keyword scoring). Your job is to select the most relevant topics and decide what depth of data to retrieve.

### TASK
1. **Analyze** the user query intent — what information are they looking for?
2. **Select** the 1-3 most relevant candidate topics.
3. **Decide depth** for each selected topic:
   - `"summary"` → The user wants a high-level overview (e.g., "What is this project about?", "Give me a summary of X")
   - `"leaf"` → The user wants specific details, facts, decisions, or code (e.g., "What library did I choose?", "What are the eligibility criteria?")

### INPUTS
- **Query**: The user's search query. May contain `[CURRENT]`, `[PREV]`, `[PREV2]` tags indicating message recency. Focus primarily on `[CURRENT]`.
- **Candidates**: A ranked list. Each entry has:
  - `[id:N]` — The database ID of this topic (use this EXACTLY in your output)
  - Path — The topic hierarchy (e.g., `VISA > Summer Internship > Database`)
  - `sim` — Vector similarity score (0-1, higher = more semantically relevant)
  - `bm25` — Keyword match score (higher = more keyword overlap)
  - `time` — When this topic was last updated
  - `leaf` — Whether this is a leaf node (True) or parent node (False)

### SELECTION RULES
1. Select **1-3 topics**. Fewer is better — only select what's truly relevant.
2. Prefer topics with **higher combined sim + bm25 scores**.
3. If a **leaf node** is selected with depth `"leaf"`, the system fetches its raw memories (facts, decisions, episodes).
4. If a **parent node** is selected with depth `"leaf"`, the system fetches its aggregated summary built from child data.
5. If depth is `"summary"`, the system returns only the topic's high-level description.
6. When in doubt between a parent and its child, prefer the **more specific (deeper)** topic.

### OUTPUT CONSTRAINTS
- Use the **exact `id`** from the `[id:N]` prefix of each candidate. Do NOT invent or guess IDs.
- Build the `chain` array by splitting the candidate's path on ` > ` (e.g., `"VISA > Summer Internship"` → `["VISA", "Summer Internship"]`).
- Output **strict JSON only** — no markdown, no explanation outside the JSON object.

### EXAMPLE
Given candidates:
- [id:4] VISA > Summer Internship > Database > Migrations | sim: 0.59 | bm25: 8.09 | leaf: True
- [id:2] VISA > Summer Internship | sim: 0.63 | bm25: 2.14 | leaf: False

For query "What are the visa internship eligibility criteria?":
```json
{{
  "reasoning": "The user wants specific eligibility details about the VISA Summer Internship. Topic id:2 covers the internship broadly.",
  "selected_topics": [
    {{"id": 2, "chain": ["VISA", "Summer Internship"], "depth": "leaf"}}
  ]
}}
```"""

USER_TEMPLATE = """Query: {query}

Candidates:
{candidates}

Select the relevant topics and decide retrieval depth."""


class AgenticRefiner:
    def __init__(self, config: RetrievalConfig):
        self.config = config
        self.ai = SafeAI()
        
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
                f"- [id:{c.topic_id}] {c.path} | sim: {c.sim_score} | bm25: {c.bm25_score} "
                f"| time: {c.timestamp} | leaf: {c.is_leaf}"
            )
            candidate_lines.append(line)
        candidates_text = "\n".join(candidate_lines)

        schema = RefinedSelection.model_json_schema()
        usr_prompt = USER_TEMPLATE.format(query=query, candidates=candidates_text)

        # Constrained decoding: LLM can only produce tokens valid under this schema
        raw_response = self.ai.generate(
            usr_prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=schema,
            retrieval=True
        )
        if not raw_response:
            return {"reasoning": "Generation failed", "selected_topics": []}
            
        try:
            selection = RefinedSelection.model_validate_json(raw_response)
            return selection.model_dump()
        except Exception as e:
            print(f"[AgenticRefiner] Validation Error: {e}")
            return {"reasoning": f"Validation Error: {str(e)}", "selected_topics": []}
