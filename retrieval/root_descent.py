import math
import re
from collections import Counter

from Database.db_setup import Topic, engine
from api_server import get_tree_cache
from retrieval.context_bridge import ContextBridge
from retrieval.node_selector import AdaptiveNodeSelector, SelectionResult
from retrieval.structs import CandidateTopic, RetrievalConfig, RetrievalContext
from sqlalchemy.orm import sessionmaker

DESCENT_SELECTION_PROMPT = """
You are a routing agent for a hierarchical knowledge system.
Input:
- User query
- Current parent path
- List of child nodes under that parent

Task:
Select the most relevant child nodes to continue descending toward the answer.

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

3. Child-node selection strategy:
   - [CHANGED FROM ROOT SEARCH] You are already inside a chosen branch, so reason locally under the current parent path.
   - [CHANGED FROM ROOT SEARCH] Select child nodes that best continue the answer path beneath the current parent, not broad neighboring domains.
   - [CHANGED FROM ROOT SEARCH] Use the parent path as a semantic frame to interpret short or ambiguous child names.
   - [CHANGED FROM ROOT SEARCH] Prefer precision over coverage, but include multiple child nodes if the answer could span parallel sub-branches.

4. Constraints:
   - Max child nodes <= {max_selected}
   - Avoid redundant child nodes
   - [CHANGED FROM ROOT SEARCH] Output the exact child node names from the provided child list only.

5. Multi-hop awareness:
   - [CHANGED FROM ROOT SEARCH] Think about which child nodes are most likely to contain the next hop inside this branch.
"""


class BM25Scorer:
    """Lightweight BM25 scorer for topic name + description."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = []
        self.doc_lens = []
        self.avg_dl = 0
        self.idf = {}
        self.N = 0

    def fit(self, documents: list[str]):
        """Build index from a list of text documents."""
        self.corpus = [self._tokenize(doc) for doc in documents]
        self.N = len(self.corpus)
        self.doc_lens = [len(doc) for doc in self.corpus]
        self.avg_dl = sum(self.doc_lens) / max(self.N, 1)

        df = Counter()
        for doc in self.corpus:
            unique_terms = set(doc)
            for term in unique_terms:
                df[term] += 1

        self.idf = {}
        for term, freq in df.items():
            self.idf[term] = math.log((self.N - freq + 0.5) / (freq + 0.5) + 1)

    def score(self, query: str, doc_idx: int) -> float:
        """Score a single document against a query."""
        query_terms = self._tokenize(query)
        doc = self.corpus[doc_idx]
        dl = self.doc_lens[doc_idx]

        tf_map = Counter(doc)
        score = 0.0

        for term in query_terms:
            if term not in self.idf:
                continue
            tf = tf_map.get(term, 0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * dl / max(self.avg_dl, 1))
            score += self.idf[term] * (numerator / denominator)

        return score

    def _tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return re.findall(r"\b\w+\b", text.lower())


class RootDescent:
    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.Session = sessionmaker(bind=engine)
        self.node_selector = AdaptiveNodeSelector()

    def descend(
        self,
        start_nodes: list[Topic],
        ctx: RetrievalContext,
        include_start_nodes: bool = False,
        force_full_subtree: bool = False,
    ) -> list[CandidateTopic]:
        """
        Phase 3: Lean descent.
        Returns top-k candidates combined from multiple starting nodes.
        No descriptions or summaries are sent to the refiner.
        """
        if not start_nodes:
            return []

        session = self.Session()
        all_candidates = []
        seen_ids = set()

        try:
            for start_node in start_nodes:
                node = session.get(Topic, start_node.id)
                if not node:
                    continue

                current_path = self._build_path(node)
                if include_start_nodes:
                    self._append_candidate(
                        node=node,
                        query_vec=ctx.query_vector,
                        candidates=all_candidates,
                        current_path=current_path,
                        seen_ids=seen_ids,
                        force_include=True,
                        selected_boost=True,
                    )

                self._recursive_collect(
                    node=node,
                    ctx=ctx,
                    candidates=all_candidates,
                    current_path=current_path,
                    seen_ids=seen_ids,
                    force_full_subtree=force_full_subtree,
                )

            if not all_candidates:
                return []

            bm25 = BM25Scorer()
            docs = [candidate["description"] for candidate in all_candidates]
            bm25.fit(docs)

            for idx, candidate in enumerate(all_candidates):
                raw_bm25 = bm25.score(ctx.query_text, idx)
                candidate["bm25_score"] = min(raw_bm25 / 15.0, 1.0)
                candidate["combined"] = (candidate["sim_score"] * 0.8) + (candidate["bm25_score"] * 0.2)

            all_candidates.sort(key=lambda item: item["combined"], reverse=True)
            top_k = all_candidates[:self.config.top_k]

            results = []
            for candidate in top_k:
                results.append(
                    CandidateTopic(
                        name=candidate["name"],
                        path=candidate["path"],
                        topic_id=candidate["topic_id"],
                        sim_score=round(candidate["sim_score"], 3),
                        bm25_score=round(candidate["bm25_score"], 3),
                        timestamp=candidate["timestamp"],
                        is_leaf=candidate["is_leaf"],
                    )
                )

            return results
        finally:
            session.close()

    def _build_path(self, node: Topic) -> list[str]:
        tree = get_tree_cache()
        path = []
        current = node
        while current is not None:
            path.append(current.name)
            current = tree.parent_map.get(current.id)
        return list(reversed(path))

    def _append_candidate(
        self,
        node: Topic,
        query_vec,
        candidates: list[dict],
        current_path: list[str],
        seen_ids: set[int],
        force_include: bool = False,
        selected_boost: bool = False,
    ) -> bool:
        tree = get_tree_cache()
        if node.id in seen_ids:
            return False

        sim_score = 0.0
        if query_vec is not None:
            node_vec = tree.embedding_cache.get(node.id)
            if node_vec is not None:
                sim_score = self.bridge._cosine_similarity(query_vec, node_vec)
            elif not force_include:
                return False

        if selected_boost:
            sim_score = max(sim_score, 1.0)

        if not force_include and sim_score < self.config.descent_threshold:
            return False

        path_str = " > ".join(current_path)
        ts = node.timestamp.strftime("%Y-%m-%d %H:%M") if node.timestamp else "?"
        is_leaf = not bool(tree.children_map.get(node.id))

        candidates.append(
            {
                "name": node.name,
                "path": path_str,
                "topic_id": node.id,
                "sim_score": float(sim_score),
                "bm25_score": 0.0,
                "description": f"{path_str} {node.description or ''}".strip(),
                "timestamp": ts,
                "is_leaf": is_leaf,
            }
        )
        seen_ids.add(node.id)
        return True

    def _recursive_collect(
        self,
        node: Topic,
        ctx: RetrievalContext,
        candidates: list[dict],
        current_path: list[str],
        seen_ids: set[int],
        force_full_subtree: bool = False,
    ):
        """Recursively collect scored candidates from the topic tree."""
        tree = get_tree_cache()
        children = tree.children_map.get(node.id)
        if not children:
            return

        selection = self._select_children(node, children, ctx, current_path, force_full_subtree)
        selected_ids = {child.id for child in selection.nodes}
        llm_selected = selection.strategy == "llm"

        for child in selection.nodes:
            child_path = current_path + [child.name]
            added = self._append_candidate(
                node=child,
                query_vec=ctx.query_vector,
                candidates=candidates,
                current_path=child_path,
                seen_ids=seen_ids,
                force_include=force_full_subtree or (llm_selected and child.id in selected_ids),
            )

            if force_full_subtree or added or (llm_selected and child.id in selected_ids):
                self._recursive_collect(
                    node=child,
                    ctx=ctx,
                    candidates=candidates,
                    current_path=child_path,
                    seen_ids=seen_ids,
                    force_full_subtree=force_full_subtree,
                )

    def _select_children(
        self,
        node: Topic,
        children: list[Topic],
        ctx: RetrievalContext,
        current_path: list[str],
        force_full_subtree: bool,
    ) -> SelectionResult:
        if force_full_subtree:
            return SelectionResult(nodes=list(children), strategy="vector")

        max_selected = min(len(children), self.config.llm_max_selected)
        return self.node_selector.select_nodes(
            query_text=(ctx.query_text or ctx.current_prompt or "").strip(),
            nodes=children,
            max_selected=max_selected,
            llm_trigger_count=self.config.llm_choice_threshold,
            threshold=self.config.descent_threshold,
            vector_score=lambda child: self._node_vector_score(ctx.query_vector, child),
            system_prompt=DESCENT_SELECTION_PROMPT.format(max_selected=max_selected),
            user_prompt_builder=lambda query_text, batch: self._build_child_prompt(
                query_text=query_text,
                parent=node,
                current_path=current_path,
                children=batch,
            ),
            vector_limit=None,
        )

    def _node_vector_score(self, query_vec, node: Topic) -> float | None:
        if query_vec is None:
            return None

        tree = get_tree_cache()
        node_vec = tree.embedding_cache.get(node.id)
        if node_vec is None:
            return None
        return self.bridge._cosine_similarity(query_vec, node_vec)

    def _build_child_prompt(
        self,
        *,
        query_text: str,
        parent: Topic,
        current_path: list[str],
        children: list[Topic],
    ) -> str:
        child_lines = "\n".join(child.name for child in children if child.name)
        parent_path = " > ".join(current_path) if current_path else parent.name
        return (
            f"Query context:\n{query_text}\n"
            f"Current parent path:\n{parent_path}\n"
            f"Child nodes:\n{child_lines}\n"
        )
