import json
import numpy as np
from Database.db_manager import DatabaseManager
from Database.db_setup import Topic
from retrieval.context_bridge import ContextBridge
from retrieval.structs import RetrievalConfig, RetrievalContext
from safe_ai import SafeAI
from typing import List
from pydantic import BaseModel, Field

MAX_ROOTS_PER_CALL = 100
MAX_SELECTED_ROOTS = 6
FIRST_PASS_SELECTIONS = 3

class Roots(BaseModel):
    roots: List[str] = Field(..., description="name of the roots where the correct answer path is most likely to exist.")


SYSTEM_PROMPT = """
You are a routing agent for a hierarchical knowledge system.
Input:
- User query
- List of root nodes

Task:
Select 3–5 most relevant roots where the answer is likely to exist.

Rules:

1. Normalize query:
   - Remove fluff (e.g., "remind me", "I was wondering")
   - Extract:
     • Core Intent (recall / explain / plan / recommend / compare)
     • Domain (travel, food, business, tech, etc.)
     • Key Entities (places, objects, systems, roles)

2. Expand understanding:
   - Do NOT rely only on surface keywords
   - Interpret the query in broader semantic terms
   - Identify the *environment or system* the query belongs to
     Examples:
       - "factory" → business / industry
       - "human development" → business / organization / HR
       - "restaurant menu" → food / hospitality

3. Root selection strategy:
   - Select roots based on where structured knowledge would exist
   - Prefer roots that contain the *contextual system*, not just keywords
   - Always return 3–5 roots (never just 1)
   - If unsure, include adjacent or parent domains
   - Favor coverage over precision when ambiguity exists

4. Constraints:
   - Max roots ≤ {max_selected}
   - Avoid redundant roots
   - Do not overfit to narrow interpretations
   - From the list of the roots output the exact name of the roots selected.

5. Multi-hop awareness:
   - If query spans multiple concepts, include roots covering each step
   - Example: "startup hiring engineers in Germany"
     → business + jobs + geography
"""
class RootSearch:
    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session
        self.ai = SafeAI()

    def scan(self, ctx: RetrievalContext, top_k=6) -> list[Topic]:
        """
        Phase 2: The Broad Root Scan.
        Primary path: LLM reasoning over root names using ContextBridge's merged query_text.
        Fallback path: vector similarity if the LLM fails.
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

            llm_roots = self._scan_with_llm(ctx, roots, top_k=min(top_k, MAX_SELECTED_ROOTS))
            if llm_roots:
                for root in llm_roots:
                    session.expunge(root)
                return llm_roots

            return self._scan_with_vectors(session, ctx, roots, top_k=top_k)
        finally:
            session.close()

    def _scan_with_llm(self, ctx: RetrievalContext, roots: list[Topic], top_k: int) -> list[Topic]:
        query_text = (ctx.query_text or ctx.current_prompt or "").strip()
        if not query_text:
            return []

        candidates = roots
        first_pass_limit = max(1, min(FIRST_PASS_SELECTIONS, top_k, MAX_ROOTS_PER_CALL - 1))

        while len(candidates) > MAX_ROOTS_PER_CALL:
            reduced = []
            seen_ids = set()

            for start in range(0, len(candidates), MAX_ROOTS_PER_CALL):
                batch = candidates[start:start + MAX_ROOTS_PER_CALL]
                selected = self._select_roots_batch(query_text, batch, min(first_pass_limit, len(batch)))
                for root in selected:
                    if root.id not in seen_ids:
                        reduced.append(root)
                        seen_ids.add(root.id)

            if not reduced or len(reduced) >= len(candidates):
                return []

            candidates = reduced

        return self._select_roots_batch(query_text, candidates, min(top_k, len(candidates)))

    def _select_roots_batch(self, query_text: str, roots: list[Topic], max_selected: int) -> list[Topic]:
        if not roots or max_selected <= 0:
            return []
        root_name, root_dict = [], {}
        for root in roots:
            nm = (root.name).lower()
            root_name.append(nm)
            root_dict[nm] = root

        root_lines = "\n".join(root_name)
        user_prompt = (
            f"Query context:\n{query_text}\n"
            f"Root nodes:\n{root_lines}\n"
        )

        raw_response = self.ai.generate(
            user_prompt,
            system_prompt=SYSTEM_PROMPT.format(max_selected=max_selected),
            json_schema=Roots.model_json_schema(),
        )
        if not raw_response:
            return []

        try:
            Roots.model_validate_json(raw_response)
            data = json.loads(self._extract_json(raw_response))
            chosen_roots = data.get("roots", [])
        except Exception as e:
            print(f"[RootSearch] LLM parse failed: {e}")
            return []

        selected_roots = []
        for crt in chosen_roots:
            crt = crt.lower()
            root = root_dict[crt]
            if crt not in root_dict or root is None:
                continue

            selected_roots.append(root)
        return selected_roots

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]
        return text

    def _scan_with_vectors(self, session, ctx: RetrievalContext, roots: list[Topic], top_k: int) -> list[Topic]:
        query_vec = ctx.query_vector
        if query_vec is None:
            return []

        valid_roots = []
        root_vecs = []
        for root in roots:
            if root.embedding is not None:
                valid_roots.append(root)
                root_vecs.append(self.db_manager._from_blob(root.embedding))

        if not valid_roots:
            return []

        root_matrix = np.array(root_vecs)
        vec_scores = np.dot(root_matrix, query_vec)

        scored_roots = []
        for i, root in enumerate(valid_roots):
            vec_score = float(vec_scores[i])
            if vec_score >= self.config.root_threshold:
                scored_roots.append((vec_score, root))

        scored_roots.sort(key=lambda x: x[0], reverse=True)
        top = scored_roots[:top_k]

        for vec_s, root in top:
            print(f"[DEBUG] Root fallback: {root.name} | Score: {vec_s:.4f}")

        result = [item[1] for item in top]
        for root in result:
            session.expunge(root)

        return result

