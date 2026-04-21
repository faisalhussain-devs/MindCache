from retrieval.structs import RetrievalConfig, RefinedSelection, CandidateTopic
from Memory_extract.safe_ai import SafeAI

SYSTEM_PROMPT = """You are the Retrieval Refinement Engine for a hierarchical knowledge graph.
You receive a user query and up to 20 ranked candidate topics. Your job: select the 1-3 BEST topics and decide retrieval depth.

## STEP 1: CLASSIFY QUERY INTENT
Before selecting, determine what the user actually needs:
- RECALL → specific fact, decision, name, date, config ("What did I decide?", "Which library?")
- EXPLAIN → understanding of a concept or system ("How does X work?", "Why did we do Y?")
- COMPARE → contrast between options ("X vs Y", "differences between")
- PLAN → future actions, todos, next steps ("What should I do next?", "roadmap for")
- OVERVIEW → broad summary of a topic ("Tell me about X", "What is project Y?")

This intent determines depth:
- RECALL, COMPARE → always "leaf" (need raw facts)
- EXPLAIN → "leaf" for specific mechanisms, "summary" for broad concepts
- PLAN → "leaf" (need actionable details)
- OVERVIEW → "summary"

## STEP 2: READING THE CANDIDATE LIST

Each candidate is shown as:
  [id:ROOT_ID]RootName > [id:MID_ID]MidName > [id:LEAF_ID]LeafName | ...

EVERY [id:N] in the path is a valid, selectable node — not just the leaf.
The rightmost node is the candidate matched by search. The nodes to its left are its ancestors.

## STEP 3: SELECT TOPICS (CRITICAL RULES)

RULE 1 — SELECT 1-3 TOPICS. Fewer is better. If one topic clearly answers the query, select only one.

RULE 2 — PARENT vs CHILD DEDUP (IMPORTANT):
  If BOTH a parent and its child appear as candidates:
  - If the query is specific → select the CHILD only (more precise).
  - If the query is broad → select the PARENT only (covers more ground).
  - NEVER select both a parent and its descendant. That wastes a selection slot.

RULE 3 — SIBLING SELECTION:
  If two siblings under the same parent are relevant:
  - Select both only if the query genuinely spans both sub-topics.
  - Otherwise select the single most relevant sibling.

RULE 4 — CROSS-DOMAIN:
  If the query spans two unrelated domains:
  - Select one topic from each relevant domain. Max 2 domains.

RULE 5 — NOISE REJECTION:
  With up to 20 candidates, many will be irrelevant padding. Reject aggressively.
  If a candidate's path has no semantic connection to the query intent, skip it regardless of scores.

RULE 6 — ANCESTOR ELEVATION (KEY RULE):
  If you see that MULTIPLE candidates all share the same ancestor (same [id:N] prefix in their path)
  AND the query is OVERVIEW or broadly EXPLAIN:
  → Select the ANCESTOR node instead of any individual leaf.
  → Use the ancestor's ID (e.g., [id:50]) as your selected id.
  → Set depth to "summary" — this returns the pre-built recursive summary of the whole sub-topic.
  → This collapses N leaf selections into 1 high-level answer. Always prefer this for broad queries.
  EXAMPLE: if 8 candidates all start with [id:42]ProductComparator and the query is "tell me about
  ProductComparator", select id:42 with depth:"summary" instead of 8 individual leaves.

RULE 7 — TEMPORAL DECAY (RECENCY BIAS):
  Every candidate shows a timestamp (time: YYYY-MM-DD HH:MM). Memory degrades with age.
  - When two candidates are comparable in relevance, ALWAYS prefer the more recent one.
  - A memory from last week is stronger evidence of current state than one from 6 months ago.
  - If the query is about current state/status/decisions ("what am I doing now?", "current plan") →
    strongly favor memories from the last 30 days. Older ones may be outdated.
  - If the query is historical ("what did I decide in March?", "original design") → recency bias
    does NOT apply — use the timestamp to find the historically relevant memory instead.
  - Exception: if an old memory is the ONLY relevant one, select it regardless of age.

## STEP 4: ASSIGN DEPTH
For each selected topic:
- "leaf" → System fetches raw memories: facts, decisions, episodes, timestamps. Use for specific queries.
- "summary" → System fetches the node's high-level recursive summary. Use for overview/broad queries.

## OUTPUT FORMAT
- Use EXACT ids from the [id:N] tokens. Do NOT invent IDs.
- chain: list of name segments for the path to the SELECTED node only (not the full candidate path).
  e.g. if you select [id:50] from path "[id:1]CS > [id:50]DigitalLogic > [id:204]Counters", chain=["CS","DigitalLogic"]
- Output strict JSON only.

## EXAMPLE
Candidates:
- [id:1]Engineering > [id:12]Backend > [id:42]Database > [id:88]Migrations | ce: 4.21 | bm25: 0.45 | from: 2026-01-10 to: 2026-03-10 | leaf: True
- [id:1]Engineering > [id:12]Backend > [id:42]Database > [id:91]Indexes | ce: 3.80 | bm25: 0.30 | from: 2026-02-01 to: 2026-03-09 | leaf: True
- [id:2]Frontend > [id:45]Components > [id:99]Forms | ce: 0.32 | bm25: 0.05 | from: 2026-01-05 to: 2026-02-20 | leaf: True

Query: "What migration strategy did we decide on?"
Intent: RECALL → needs "leaf", specific question → pick leaf [id:88], not parent.
Analysis: id:88 direct hit. id:91 Indexes unrelated. id:99 noise.

{{
  "reasoning": "RECALL intent. User asks about a specific migration decision. id:88 covers Migrations directly. Skipping id:91 (unrelated) and id:99 (noise).",
  "selected_topics": [
    {{"id": 88, "chain": ["Engineering", "Backend", "Database", "Migrations"], "depth": "leaf"}}
  ]
}}

Query: "Give me an overview of the Database."
Intent: OVERVIEW → ANCESTOR ELEVATION applies. id:88 and id:91 both live under [id:42]Database.
Select ancestor id:42 with depth:"summary".

{{
  "reasoning": "OVERVIEW intent. Two candidates cluster under [id:42]Database. Elevating to the parent for its recursive summary.",
  "selected_topics": [
    {{"id": 42, "chain": ["Engineering", "Backend", "Database"], "depth": "summary"}}
  ]
}}"""

USER_TEMPLATE = """Query: {query}

Candidates ({count} total):
{candidates}

Classify intent, apply RULE 6 (ancestor elevation) for broad queries, apply RULE 7 (prefer recent memories unless query is historical), filter noise, deduplicate, then select 1-3 topics with depth."""


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

        # Build candidate lines using id_path (inline ancestor IDs) for the LLM
        candidate_lines = []
        for c in candidates:
            line = (
                f"- {c.path} | ce: {c.cross_encoder_score} | bm25: {round(c.rrf_score, 4)} "
                f"| from: {c.timestamp_start} to: {c.timestamp_end} | leaf: {c.is_leaf}"
            )
            candidate_lines.append(line)
        candidates_text = "\n".join(candidate_lines)

        schema = RefinedSelection.model_json_schema()
        usr_prompt = USER_TEMPLATE.format(query=query, candidates=candidates_text, count=len(candidates))

        # Constrained decoding: LLM can only produce tokens valid under this schema
        try:
            raw_response = self.ai.generate(
                usr_prompt,
                system_prompt=SYSTEM_PROMPT,
                json_schema=schema,
                retrieval=True
            )
        except Exception as e:
            print(f"[AgenticRefiner] AI Generation Error: {e}")
            raw_response = None

        if not raw_response:
            return self._fallback_selection(candidates, "Generation failed")
            
        try:
            selection = RefinedSelection.model_validate_json(raw_response)
            return selection.model_dump()
        except Exception as e:
            print(f"[AgenticRefiner] Validation Error: {e}")
            return self._fallback_selection(candidates, f"Validation Error: {str(e)}")

    def _fallback_selection(self, candidates: list[CandidateTopic], reason: str) -> dict:
        print(f"[AgenticRefiner] {reason}. Falling back to top 3 candidates from vector search.")
        selection = []
        for c in candidates[:3]:
            chain = c.path.split(" > ") if c.path else [c.name]
            selection.append({
                "id": c.topic_id,
                "chain": chain,
                "depth": "leaf" if c.is_leaf else "summary"
            })
        return {
            "reasoning": f"{reason}. Used vector search fallback.",
            "selected_topics": selection
        }
