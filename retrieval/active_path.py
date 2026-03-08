import json
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

    def retrieve(self, current_prompt: str, last_msg: str = None, prev_msg: str = None) -> RetrievalResult:
        """
        Execute the full retrieval pipeline:
        1. Context Bridge → query vector
        2. Root Search → best root node
        3. Root Descent → top-k candidates (lean)
        4. Refiner → selected topics + depth
        5. DB Fetch → actual data (summary or leaf memories)
        """
        # Phase 1: Context Bridge
        ctx = self.bridge.process(current_prompt, last_msg, prev_msg)
        
        # Phase 2: Root Search
        root_nodes = self.search.scan(ctx)
        if not root_nodes:
            return RetrievalResult(context="No relevant long-term memory found.")

        # Phase 3: Root Descent (lean top-k)
        candidates = self.descent.descend(root_nodes, ctx)
        if not candidates:
            return RetrievalResult(context="No matching topics found in descent.")

        # Phase 4: Refiner + Depth Decision
        refined = self.refiner.refine(
            query=ctx.query_text,
            candidates=candidates
        )
        selected = refined.get("selected_topics", [])
        if not selected:
            return RetrievalResult(
                context=f"Refiner found no relevant topics. Reasoning: {refined.get('reasoning', '')}"
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
                context="\n\n".join(context_parts)
            )
        finally:
            session.close()

    def _find_topic_by_chain(self, session, chain: list) -> Optional[Topic]:
        """Walk the topic tree to find the node matching the chain."""
        if not chain:
            return None
        
        current = None
        for level, name in enumerate(chain):
            query = session.query(Topic).filter(
                Topic.name.ilike(name), Topic.level == level
            )
            if current:
                query = query.filter(Topic.parent_id == current.id)
            else:
                query = query.filter(Topic.parent_id.is_(None))
            
            current = query.first()
            if not current:
                return None
        
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

