import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from pydantic import BaseModel, Field
from Memory_extract.safe_ai import SafeAI

MAX_NODES_PER_CALL = 100
FIRST_PASS_SELECTIONS = 3


class SelectedNodeNames(BaseModel):
    nodes: list[str] = Field(
        ...,
        description="Exact names of the selected nodes from the provided candidate list.",
    )


@dataclass
class SelectionResult:
    nodes: list[Any]
    strategy: str


class AdaptiveNodeSelector:
    def __init__(self, ai: SafeAI | None = None):
        self.ai = ai or SafeAI()

    def select_nodes(
        self,
        *,
        query_text: str,
        nodes: Sequence[Any],
        max_selected: int,
        llm_trigger_count: int,
        threshold: float,
        vector_score: Callable[[Any], float | None],
        system_prompt: str,
        user_prompt_builder: Callable[[str, Sequence[Any]], str],
        name_getter: Callable[[Any], str] | None = None,
        vector_limit: int | None = None,
    ) -> SelectionResult:
        node_list = list(nodes)
        if not node_list or max_selected <= 0:
            return SelectionResult(nodes=[], strategy="vector")

        clean_query = (query_text or "").strip()
        if len(node_list) > llm_trigger_count and clean_query:
            llm_nodes = self._select_with_llm(
                query_text=clean_query,
                nodes=node_list,
                max_selected=max_selected,
                system_prompt=system_prompt,
                user_prompt_builder=user_prompt_builder,
                name_getter=name_getter,
            )
            if llm_nodes:
                return SelectionResult(nodes=llm_nodes, strategy="llm")

        vector_nodes = self._select_with_vectors(
            nodes=node_list,
            threshold=threshold,
            vector_score=vector_score,
            limit=vector_limit,
        )
        return SelectionResult(nodes=vector_nodes, strategy="vector")

    def _select_with_llm(
        self,
        *,
        query_text: str,
        nodes: list[Any],
        max_selected: int,
        system_prompt: str,
        user_prompt_builder: Callable[[str, Sequence[Any]], str],
        name_getter: Callable[[Any], str] | None = None,
    ) -> list[Any]:
        candidates = list(nodes)
        first_pass_limit = max(1, min(FIRST_PASS_SELECTIONS, max_selected, MAX_NODES_PER_CALL - 1))

        while len(candidates) > MAX_NODES_PER_CALL:
            reduced = []
            seen_ids = set()

            for start in range(0, len(candidates), MAX_NODES_PER_CALL):
                batch = candidates[start:start + MAX_NODES_PER_CALL]
                selected = self._select_batch(
                    query_text=query_text,
                    nodes=batch,
                    max_selected=min(first_pass_limit, len(batch)),
                    system_prompt=system_prompt,
                    user_prompt_builder=user_prompt_builder,
                    name_getter=name_getter,
                )
                for node in selected:
                    node_id = getattr(node, "id", id(node))
                    if node_id in seen_ids:
                        continue
                    reduced.append(node)
                    seen_ids.add(node_id)

            if not reduced or len(reduced) >= len(candidates):
                return []

            candidates = reduced

        return self._select_batch(
            query_text=query_text,
            nodes=candidates,
            max_selected=min(max_selected, len(candidates)),
            system_prompt=system_prompt,
            user_prompt_builder=user_prompt_builder,
            name_getter=name_getter,
        )

    def _select_batch(
        self,
        *,
        query_text: str,
        nodes: list[Any],
        max_selected: int,
        system_prompt: str,
        user_prompt_builder: Callable[[str, Sequence[Any]], str],
        name_getter: Callable[[Any], str] | None = None,
    ) -> list[Any]:
        if not nodes or max_selected <= 0:
            return []

        node_name = name_getter or (lambda node: getattr(node, "name", ""))
        node_lines = []
        node_lookup: dict[str, list[Any]] = {}

        for node in nodes:
            raw_name = (node_name(node) or "").strip()
            if not raw_name:
                continue
            node_lines.append(raw_name)
            node_lookup.setdefault(raw_name.lower(), []).append(node)

        if not node_lines:
            return []

        user_prompt = user_prompt_builder(query_text, nodes)
        raw_response = self.ai.generate(
            user_prompt,
            system_prompt=system_prompt,
            json_schema=SelectedNodeNames.model_json_schema(),
            retrieval=True,
        )
        if not raw_response:
            return []

        try:
            data = json.loads(self._extract_json(raw_response))
            chosen_names = SelectedNodeNames.model_validate(data).nodes
        except Exception as exc:
            print(f"[AdaptiveNodeSelector] LLM parse failed: {exc}")
            return []

        selected_nodes = []
        seen_ids = set()
        for chosen_name in chosen_names:
            bucket = node_lookup.get((chosen_name or "").strip().lower())
            if not bucket:
                continue

            node = bucket.pop(0)
            node_id = getattr(node, "id", id(node))
            if node_id in seen_ids:
                continue
            selected_nodes.append(node)
            seen_ids.add(node_id)

        return selected_nodes[:max_selected]

    def _select_with_vectors(
        self,
        *,
        nodes: list[Any],
        threshold: float,
        vector_score: Callable[[Any], float | None],
        limit: int | None = None,
    ) -> list[Any]:
        scored_nodes = []
        for node in nodes:
            score = vector_score(node)
            if score is None or score < threshold:
                continue
            scored_nodes.append((float(score), node))

        scored_nodes.sort(key=lambda item: item[0], reverse=True)
        if limit is not None:
            scored_nodes = scored_nodes[:limit]
        return [node for _, node in scored_nodes]

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]
        return text
