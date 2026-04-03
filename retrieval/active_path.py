import json
from collections import defaultdict
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from Database.embedder import EmbeddingManager
from Database.db_setup import (
    engine, Topic, EpisodicMemory,
    UserMemory, KnowledgeMemory, DecisionMemory
)
from retrieval.structs import RetrievalConfig, RetrievalResult
from retrieval.context_bridge import ContextBridge
from retrieval.root_search import RootSearch
from retrieval.root_descent import RootDescent
from retrieval.agentic_refiner import AgenticRefiner
from Database.nodes_summary import RecursiveSummarizer
from api_server import get_tree_cache

class ActivePathRetrieval:
    def __init__(self, config: Optional[RetrievalConfig] = None):
        if config is None:
            config = RetrievalConfig()
        self.config = config
        self.Session = sessionmaker(bind=engine)
        self.summarizer = RecursiveSummarizer()
        
        # Initialize Shared Resources
        self.embedder = EmbeddingManager()
        
        # Initialize Phases
        self.bridge = ContextBridge(self.embedder, self.config)
        self.search = RootSearch(self.config, self.bridge)
        self.descent = RootDescent(self.config, self.bridge)
        self.refiner = AgenticRefiner(self.config)

    def retrieve(
        self,
        current_prompt: str,
        last_msg: str | None = None,
        prev_msg: str | None = None,
        selected_nodes_by_level: dict[str, list[int]] | None = None,
    ) -> RetrievalResult:
        """
        Execute the full retrieval pipeline:
        1. Context Bridge → query vector
        2. Optional user-constrained path resolution
        3. Root Search or constrained descent start
        4. Refiner → selected topics + depth
        5. DB Fetch → actual data (summary or leaf memories)
        """
        selected_nodes_by_level = self._normalize_selected_nodes_by_level(selected_nodes_by_level)
        # Phase 1: Context Bridge
        ctx = self.bridge.process(current_prompt, last_msg=last_msg, prev_msg=prev_msg)
        trace = {
            "selected_nodes_by_level": {},
            "constraint_path_ids": [],
            "starting_node_ids": [],
            "root_ids": [],
            "candidate_topic_ids": [],
            "selected_topic_ids": [],
            "selected_topics": [],
        }

        constrained = self._resolve_selected_nodes(selected_nodes_by_level)
        trace["selected_nodes_by_level"] = {
            str(level): ids for level, ids in constrained["selected_nodes_by_level"].items()
        }
        trace["constraint_path_ids"] = constrained["constraint_path_ids"]
        trace["starting_node_ids"] = constrained["starting_node_ids"]

        # Phase 2/3: Either honor the user-selected path or let Root Search choose roots
        if constrained["starting_nodes"]:
            trace["root_ids"] = constrained["root_ids"]
            candidates = self.descent.descend(
                constrained["starting_nodes"],
                ctx,
                include_start_nodes=True,
                force_full_subtree=False,
            )
        else:
            root_nodes = self.search.scan(ctx)
            trace["root_ids"] = [root.id for root in root_nodes]
            if not root_nodes:
                return RetrievalResult(
                    context="No relevant long-term memory found.",
                    trace=trace,
                )

            candidates = self.descent.descend(root_nodes, ctx)

        trace["candidate_topic_ids"] = [candidate.topic_id for candidate in candidates]
        if not candidates:
            return RetrievalResult(
                context="No matching topics found in descent.",
                trace=trace,
            )

        # Phase 4: Refiner + Depth Decision
        refined = self.refiner.refine(
            query=ctx.query_text,
            candidates=candidates
        )
        selected = refined.get("selected_topics", [])
        trace["selected_topics"] = selected
        trace["selected_topic_ids"] = [item.get("id") for item in selected if item.get("id")]
        if not selected:
            return RetrievalResult(
                context=f"Refiner found no relevant topics. Reasoning: {refined.get('reasoning', '')}",
                trace=trace,
            )

        # Phase 5: Fetch data based on depth
        context_parts = []
        session = self.Session()
        
        try:
            for item in selected:
                topic_id = item.get("id")
                chain = item.get("chain", [])
                depth = item.get("depth", "summary")
                
                # Primary: direct ID lookup (fast, reliable)
                topic = None
                if topic_id:
                    topic = session.get(Topic, topic_id)
                
                # Fallback: walk chain if ID lookup failed
                if not topic and chain:
                    topic = self._find_topic_by_chain(session, chain)
                
                if not topic:
                    continue
                
                if depth == "leaf":
                    data = self._fetch_leaf_data(session, topic)
                    context_parts.append(f"Detailed: {data}")
                else:
                    data = topic.summary or topic.description or "(No summary available)"
                    context_parts.append(f"Summary: {data}")

            return RetrievalResult(
                context="\n\n".join(context_parts),
                trace=trace,
            )
        finally:
            session.close()

    def _normalize_selected_nodes_by_level(
        self,
        selected_nodes_by_level: dict[str, list[int]] | None,
    ) -> dict[int, list[int]]:
        if not isinstance(selected_nodes_by_level, dict):
            return {}

        normalized = {}
        for level_key, node_ids in selected_nodes_by_level.items():
            try:
                level = int(level_key)
            except (TypeError, ValueError):
                continue

            clean_ids = []
            seen_ids = set()
            if not node_ids:
                continue
            for node_id in node_ids:
                try:
                    clean_id = int(node_id)
                except (TypeError, ValueError):
                    continue
                if clean_id in seen_ids:
                    continue
                clean_ids.append(clean_id)
                seen_ids.add(clean_id)

            if clean_ids:
                normalized[level] = clean_ids

        return dict(sorted(normalized.items()))

    def _resolve_selected_nodes(self, selected_nodes_by_level: dict[int, list[int]]) -> dict:
        result = {
            "selected_nodes_by_level": {},
            "constraint_path_ids": [],
            "starting_node_ids": [],
            "starting_nodes": [],
            "root_ids": [],
        }
        if not selected_nodes_by_level:
            return result

        requested_ids = []
        seen_ids = set()
        for node_ids in selected_nodes_by_level.values():
            for node_id in node_ids:
                if node_id in seen_ids:
                    continue
                requested_ids.append(node_id)
                seen_ids.add(node_id)

        if not requested_ids:
            return result

        session = self.Session()
        try:
            selected_topics = (
                session.query(Topic)
                .filter(Topic.id.in_(requested_ids))
                .all()
            )
            if not selected_topics:
                return result

            topic_by_id = {topic.id: topic for topic in selected_topics}
            actual_by_level = defaultdict(set)
            path_ids = set()
            root_ids = set()
            shadowed_ids = set()

            for topic in selected_topics:
                chain = self._topic_chain(topic)
                chain_ids = []
                for chain_node in chain:
                    chain_ids.append(chain_node.id)
                    actual_by_level[chain_node.level].add(chain_node.id)
                path_ids.update(chain_ids)
                if chain_ids:
                    root_ids.add(chain_ids[0])
                for ancestor_id in chain_ids[:-1]:
                    if ancestor_id in topic_by_id:
                        shadowed_ids.add(ancestor_id)

            starting_ids = [topic.id for topic in selected_topics if topic.id not in shadowed_ids]
            starting_nodes = [topic_by_id[node_id] for node_id in starting_ids]
            starting_nodes.sort(key=lambda topic: (topic.level, (topic.name or "").lower(), topic.id))

            for topic in starting_nodes:
                session.expunge(topic)

            result["selected_nodes_by_level"] = {
                level: sorted(node_ids)
                for level, node_ids in sorted(actual_by_level.items())
            }
            result["constraint_path_ids"] = sorted(path_ids)
            result["starting_node_ids"] = [topic.id for topic in starting_nodes]
            result["starting_nodes"] = starting_nodes
            result["root_ids"] = sorted(root_ids)
            return result
        finally:
            session.close()

    def _topic_chain(self, topic: Topic) -> list[Topic]:
        tree = get_tree_cache()
        chain = []
        current = topic
        while current is not None:
            chain.append(current)
            current = tree.parent_map.get(current.id)
        return list(reversed(chain))

    def _find_topic_by_chain(self, session, chain: list) -> Optional[Topic]:
        """Walk the cached topic tree to find the node matching the chain."""
        if not chain:
            return None
        
        tree = get_tree_cache()
        current = None
        for name in chain:
            parent_id = current.id if current else None
            pool = tree.children_map.get(parent_id, [])
            match = next((t for t in pool if (t.name or "").lower() == name.lower()), None)
            if not match:
                return None
            current = match
        
        return current

    def _fetch_leaf_data(self, session, topic: Topic) -> str:
        """
        Fetch leaf data for a topic.
        If summary exists, parse it and filter decisions to only active/conditional.
        Otherwise, query memory tables directly with the same filter.
        """
        RETRIEVAL_STATUSES = {"active", "conditional"}

        # Path 1: Pre-built summary exists — parse and filter
        if topic.summary:
            try:
                data = json.loads(topic.summary)
                decisions = data.get("decisions", {})

                # Filter decisions: only active/conditional
                filtered_decisions = {}
                for ts_key, decs in decisions.items():
                    for dec_id, content in decs.items():
                        # Format: "[Decision:status] content | Context: ..."
                        for status in RETRIEVAL_STATUSES:
                            if f"[Decision:{status}]" in content:
                                if ts_key not in filtered_decisions:
                                    filtered_decisions[ts_key] = {}
                                filtered_decisions[ts_key][dec_id] = content
                                break

                if filtered_decisions:
                    data["decisions"] = filtered_decisions
                else:
                    data["decisions"] = {}
                summary = self.summarizer._format_leaf_summary(data)
                return summary

            except (json.JSONDecodeError, AttributeError):
                return topic.summary  # Fallback to raw if parsing fails

        # Path 2: No summary — fetch directly from memory tables
        lines = []
        for Model, label in [(EpisodicMemory, 'Episodic'), (UserMemory, 'User'), (KnowledgeMemory, 'Knowledge')]:
            mems = (
                session.query(Model)
                .filter(Model.topic_id == topic.id)
                .order_by(Model.timestamp.desc())
                .all()
            )
            for m in mems:
                if m.content:
                    ts = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else '?'
                    lines.append(f"[{ts}] [{label}] {m.content}")

        # Filter decisions by status
        decisions = (
            session.query(DecisionMemory)
            .filter(
                DecisionMemory.topic_id == topic.id,
                DecisionMemory.status.in_(RETRIEVAL_STATUSES)
            )
            .order_by(DecisionMemory.last_validated_at.desc())
            .all()
        )
        
        if decisions:
            lines.append("\n== DECISIONS ==")
            for d in decisions:
                if not d.content:
                    continue
                ts = (d.last_validated_at or d.timestamp or datetime.min).strftime('%Y-%m-%d %H:%M')
                ctx = f" | Context: {d.context}" if d.context else ""
                lines.append(f"[validated: {ts}] [Decision:{d.status}] {d.content}{ctx}")

        if not lines:
            return "(No memories found)"
        
        return "\n".join(lines)
