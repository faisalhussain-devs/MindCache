import re
import time
from typing import Optional
from collections import defaultdict
from sqlalchemy.orm import sessionmaker, defer
import numpy as np

from mindcache.Database.embedder import EmbeddingManager
from mindcache.Database.db_setup import (
    Topic, EpisodicMemory,
    UserMemory, KnowledgeMemory, DecisionMemory, Session
)
from mindcache.retrieval.structs import RetrievalResult
from mindcache.retrieval.context_bridge import ContextBridge
from mindcache.retrieval.root_cache import CollapsedTreeCache, get_tree_cache
from mindcache.retrieval.hybrid_search import CrossEncoderManager, calculate_rrf



# --- New Partitioned Pipeline Constants ---
TOP_K_EPISODIC   = 50    # per-type RRF pool
TOP_K_KNOWLEDGE  = 50
TOP_K_USER       = 30    # widened to ensure short user-profile memories enter Jina pool
TOP_K_DECISION   = 30
TOP_K_SUMMARIES  = 10    # broad queries only

JINA_FINAL_K_MEMORIES  = 45   # top memories out of merged Jina pool
JINA_FINAL_K_SUMMARIES = 3    # top summaries out of merged Jina pool
JINA_FINAL_INDIVIDUAL = 5  # per-type cap: user head-start of -3 → max 11 user slots, ensuring profile facts aren't crowded out
HEAVY_TOP_K_ANCHORS    = 3    # top decision anchors for BM25 expansion

MIN_BM25_THRESHOLD = 0.5   # kept for BM25 expansion pre-filter


from mindcache.Database.nodes_summary import RecursiveSummarizer

CE_TEXT_MAX_WORDS = 200  # Max words per candidate for CE input


def _truncate_for_ce(text: str) -> str:
    """Truncate text to CE_TEXT_MAX_WORDS for cross-encoder input."""
    words = text.split()
    return " ".join(words[:CE_TEXT_MAX_WORDS]) if len(words) > CE_TEXT_MAX_WORDS else text


def _get_content_only(entry) -> str:
    """Strip path prefix from entry.searchable_text to extract content/description only."""
    raw = entry.searchable_text or ""
    path_prefix = entry.path or ""
    if path_prefix and raw.startswith(path_prefix):
        return raw[len(path_prefix):].strip()
    return raw


# Keyword-based Query Classifier
# ML-based classifier (TF-IDF + LogReg) will replace this at a later stage.

_TEMPORAL_PHRASES = [
    "timeline", "in order", "sequence", "reconstruct", "chronolog",
    "when i first", "when did i", "which happened first", "before i",
    "after i", "how many days", "days passed", "between when",
    "progress in order", "event order", "earliest", "most recent",
    "started", "began", "first mention", "first time",
    "what order", "what sequence", "which order", "when was", "how long ago",
    "how long did",
]

_BROAD_PHRASES = [
    "summarize", "summary", "summaries", "overview", "compare",
    "how my understanding", "how did my", "evolved", "developed",
    "across sessions", "all about", "give me an overview", "broad",
    "explain", "how do i", "how would you", "best way to",
    "show me how", "how does", "what are all", "tell me about",
    "describe", "walk me through", "what happened with",
    "timeline", "reconstruct", "chronolog",
]

# Shared directive blocks injected into every prompt

_USER_PROFILE_DIRECTIVE = (
    "User Profile as Binding Instructions (CRITICAL): Entries in the context "
    "marked [USER PROFILE & STANDING PREFERENCES] or tagged [USER] or [DECISION] "
    "are NOT merely background. They represent the user's explicit likes, dislikes, "
    "decisions, and standing directives. You MUST honour them in every answer you give. "
    "Rules:\n"
    "  - If the user has stated a standing preference or lifestyle choice, lead with options "
    "that satisfy it and demote or omit options that contradict it. If presenting an option "
    "that contradicts a user preference, you MUST demote it explicitly as a last-resort fallback "
    "or secondary option.\n"
    "  - If the user has stated a restriction (dietary, health, tool-use, etc.), do not recommend "
    "anything that contradicts it.\n"
    "  - Treat [DECISION] entries (active decisions) as the strongest signal — the user has "
    "already decided; align your answer with that choice.\n\n"
)

_ABSTENTION_DIRECTIVE = (
    "Event-Bound Abstention (CRITICAL): If the question asks for details tied to a specific "
    "event, purchase, action, appointment, or date, you MUST verify that the "
    "retrieved context explicitly connects the fact to THAT specific event or date. If the context "
    "only contains generic advice or instructions on the same topic WITHOUT an explicit "
    "reference to that specific event or date, treat it as missing information. Use the Missing Info "
    "sentence and explicitly state which specific detail was not found in the retrieved memories. "
    "Do NOT answer using generic context when event-specific context is required.\n\n"
)

_MISSING_INFO_DIRECTIVE = (
    "Missing Info: If the answer is not present in the retrieved context, respond with: "
    "'Based on the provided context, there is no information about [requested fact].' "
    "Specify clearly what was missing rather than guessing or using related general facts.\n\n"
)

from dataclasses import dataclass
import logging
logger = logging.getLogger(__name__)

@dataclass
class QueryClassification:
    label: str
    is_temporal: bool
    is_broad: bool

def classify_query(query: str) -> QueryClassification:
    """
    Classify the query along two orthogonal dimensions:
    1. Temporal vs Non-Temporal (checked via _TEMPORAL_PHRASES)
    2. Broad/Synthesis vs Information Extraction/Fact (checked via _BROAD_PHRASES)

    Returns a QueryClassification object (a subclass of str that acts as either
    'broad_overview' or 'information_extraction' and holds boolean properties).
    """
    q = query.lower()
    is_temporal = any(p in q for p in _TEMPORAL_PHRASES)
    is_broad = any(p in q for p in _BROAD_PHRASES)
    
    val = "broad_overview" if is_broad else "information_extraction"
    return QueryClassification(val, is_temporal, is_broad)


_RECENCY_DIRECTIVE = (
    "Timestamp & Recency (ALWAYS ACTIVE): Every retrieved memory carries a "
    "[Recorded: YYYY-MM-DD HH:MM] timestamp showing when it was stored.\n"
    "Rules:\n"
    "  - EVOLUTION (same fact updated over time): If one memory is clearly a "
    "revised or updated version of an earlier memory on the exact same fact "
    "(e.g., the user changed a preference, a decision was revised, a fact was "
    "corrected), treat the NEWEST memory as the authoritative version. Mention "
    "the older value only if the change itself is relevant to the answer "
    "(e.g., 'previously X, now updated to Y').\n"
    "  - GENUINE CONFLICT (competing claims not explainable by evolution): If "
    "two memories make contradictory claims that cannot be resolved by recency "
    "alone — for example, a stored fact contradicts a user's self-report, or "
    "two memories assert opposite things with no clear update relationship — "
    "cite BOTH, note the conflict, and flag it explicitly rather than silently "
    "choosing one. Use the newest as the primary but surface the discrepancy.\n"
    "  - NON-CONFLICTING: For stable facts with no contradiction, timestamps "
    "are invisible to the answer unless the user specifically asks about timing.\n"
    "  - EVOLUTION ARC: When summarising how understanding or preferences evolved, "
    "describe the progression oldest-to-newest using the timestamps as anchors.\n\n"
)


def get_system_prompt(is_temporal: bool, is_broad: bool) -> str:
    role = "Knowledge Synthesis & Recommendation Engine" if is_broad else "Precise Memory Extraction & Recommendation Engine"
    if is_temporal:
        desc = "strict, memory-grounded assistant. Reconstruct chronological sequences and event orderings"
    elif is_broad:
        desc = "memory-grounded assistant. Synthesise a comprehensive, structured answer"
    else:
        desc = "strict, memory-grounded system. Your primary task is to locate and report the specific fact, recommendation, or detail requested"
        
    system_prompt = (
        f"SYSTEM ROLE: {role}\n"
        f"You are a {desc} using ONLY the retrieved context.\n"
        "CORE DIRECTIVES:\n"
        + _USER_PROFILE_DIRECTIVE
        + _ABSTENTION_DIRECTIVE
        + _MISSING_INFO_DIRECTIVE
        + _RECENCY_DIRECTIVE
    )

    if is_temporal:
        system_prompt += (
            "Chronological Priority: Use the [Recorded: ...] timestamps on retrieved "
            "entries to determine the sequence of events. Always anchor your answer to "
            "exact dates where available.\n"
            "First Meaningful Mention: Identify the earliest date a concept was "
            "substantially discussed, not merely touched on.\n"
            "Zero Hallucination: Do not infer dates, sequences, or durations not "
            "explicitly stated in the context.\n"
            "Missing Timestamps: If some entries lack timestamps, reason from contextual "
            "clues (e.g., concept dependency order) and flag the uncertainty.\n"
            "Fixed-Count Lists: If asked for exactly N items, scan the full context first "
            "and budget across the full timeline — do not exhaust count on early entries.\n"
            "No Clustering: Each list item must represent ONE distinct event or concept, "
            "anchored to its specific date.\n"
        )

    if is_broad:
        system_prompt += (
            "Synthesis over Extraction: Do not just list facts. Identify patterns, "
            "progressions, and relationships across topics.\n"
            "Conceptual Arc: Organise the answer around how understanding evolved — from "
            "foundational concepts to advanced applications.\n"
            "Branch Summaries First: Weight branch-level summaries (marked [BRANCH SUMMARY]) "
            "as the primary structural content; use leaf memories for supporting detail.\n"
            "Gap Acknowledgement: If an area the user likely studied has no retrieved "
            "memories, explicitly note the gap rather than speculating.\n"
        )
    else:
        system_prompt += (
            "Exact Extraction: For counts, percentages, number pairs, problem names, "
            "accuracy rates, prices, and identifiers — report the exact value from the "
            "context. Do not paraphrase numbers.\n"
            "Narrow Match: If multiple similar values appear, report the one that most "
            "narrowly matches the specific wording of the question.\n"
            "No Unnecessary Elaboration: Do not add context, explanations, or related "
            "information beyond what directly answers the question — unless the question "
            "is a recommendation question, in which case honour the user's standing "
            "preferences and provide a concise compliant suggestion.\n"
        )


    if is_temporal:
        if is_broad:
            validation = (
                "FINAL VALIDATION: Are all dates exact? Is the sequence ordered by [Recorded] timestamp? "
                "Did you check ALL [USER PROFILE] and [DECISION] entries? Is any ordering uncertainty stated? "
                "Does the answer cover the full arc? Are gaps acknowledged? "
                "For any topic with multiple memories: did you apply recency — evolution treated as update, "
                "genuine conflicts flagged with both values surfaced? "
                "If the question targets a specific event and that event's detail is absent, "
                "did you use the Missing Info sentence?"
            )
        else:
            validation = (
                "FINAL VALIDATION: Is the answer a direct, exact extraction? Are all dates exact? "
                "Is the sequence ordered by [Recorded] timestamp? Did you check ALL [USER PROFILE] and [DECISION] entries? "
                "For any topic with multiple memories: is the newest treated as authoritative for clear updates; "
                "are genuine conflicts (not explainable by recency) flagged with both values? "
                "If the question targets a specific event/date/purchase, does the retrieved context explicitly "
                "link to that exact event — if not, use the Missing Info sentence."
            )
    else:
        if is_broad:
            validation = (
                "FINAL VALIDATION: Did you read ALL [USER PROFILE] and [DECISION] entries "
                "first? Does every recommendation comply with the user's standing preferences? "
                "If a suggestion contradicts a stated preference, remove it or clearly label "
                "it a last-resort option. Does the answer cover the full arc? Are gaps acknowledged? "
                "For any topic with multiple memories: did you apply recency — evolution treated as update, "
                "genuine conflicts flagged with both values surfaced?"
            )
        else:
            validation = (
                "FINAL VALIDATION: Is the answer a direct, exact extraction? Did you check "
                "ALL [USER PROFILE] and [DECISION] entries before answering? If the question "
                "targets a specific event/date/purchase, does the retrieved context explicitly "
                "link to that exact event — if not, use the Missing Info sentence. "
                "For any topic with multiple memories: is the newest treated as authoritative "
                "for clear updates; are genuine conflicts (not explainable by recency) flagged "
                "with both values surfaced?"
            )
    system_prompt += validation
    return system_prompt


class ActivePathRetrieval:
    def __init__(self):
        self.Session = Session
        self.summarizer = RecursiveSummarizer()
        self.embedder = EmbeddingManager()
        self.bridge = ContextBridge(self.embedder)
        self._reranker: CrossEncoderManager | None = None  # lazy-loaded on first retrieve()

    @property
    def reranker(self) -> CrossEncoderManager:
        """Load the 600MB Jina reranker only on first use, not at startup."""
        if self._reranker is None:
            self._reranker = CrossEncoderManager()
        return self._reranker

    def retrieve(
        self,
        current_prompt: str,
        user_id: str = "default",
        top_k_corpus: int = 30,
        include_summaries: bool = False
    ) -> RetrievalResult:
        """
        Multi-Stage Preference-Anchored Retrieval:
        """
        t0 = time.time()

        # Phase 1: Context Bridge
        ctx = self.bridge.process(current_prompt)
        t1 = time.time()
        logger.info(f"[Retrieval] Bridge: {t1-t0:.2f}s")

        trace = {
            "root_ids": [],
            "candidate_topic_ids": [],
            "selected_topic_ids": [],
            "selected_topics": [],
            "candidate_entry_keys": [],
            "selected_entry_keys": [],
        }

        # Ensure cache is ready for this user
        tree = get_tree_cache(user_id=user_id)
        cache = CollapsedTreeCache(user_id=user_id)
        if not cache.is_ready:
            if tree is None:
                return RetrievalResult(
                    context="Memory index not ready. Please retry in a moment.",
                    trace=trace,
                )
            if not cache.load():
                cache.build_all(tree, include_summaries=include_summaries)
        data = cache.data

        if not data or not data.entries:
            return RetrievalResult(
                context="No memory nodes found in index.",
                trace=trace,
            )

        # Run base hybrid search over full pool
        query_tokens = cache._tokenize(ctx.query_text)
        bm25_scores  = cache.bm25_score_all(query_tokens)

        query_vec = ctx.query_vector
        vec_scores = cache.vector_score_all(query_vec)

        N = len(data.entries)

        bm25_ranks = np.argsort(-bm25_scores).argsort() + 1
        vec_ranks  = np.argsort(-vec_scores).argsort() + 1

        v_scores = np.where(vec_ranks > 0, 1.0 / (60.0 + vec_ranks), 0.0)
        b_scores = np.where(bm25_ranks > 0, 1.0 / (60.0 + bm25_ranks), 0.0)
        rrf_scores = (0.8 * v_scores + 0.2 * b_scores).astype(np.float32)

        t2 = time.time()
        logger.info(f"[Retrieval] BM25+Vec+RRF on {N} nodes: {t2-t1:.3f}s")

        # Classify query
        qc = classify_query(ctx.query_text)
        query_type = qc.label
        use_summaries = qc.is_broad
        show_timestamps = True  # Always show timestamps on every memory so the LLM can resolve conflicts by recency

        # Phase 2 — Partitioned Top-K Extraction
        episodic_indices = []
        knowledge_indices = []
        user_indices = []
        decision_indices = []
        summary_indices = []

        for i, entry in enumerate(data.entries):
            is_mem = (getattr(entry, "entry_type", "") == "memory")
            if is_mem:
                if entry.memory_type == "episodic":
                    episodic_indices.append(i)
                elif entry.memory_type == "knowledge":
                    knowledge_indices.append(i)
                elif entry.memory_type == "user":
                    user_indices.append(i)
                elif entry.memory_type == "decision":
                    decision_indices.append(i)
            else:
                summary_indices.append(i)

        # Sort each pool independently by rrf_score (descending)
        episodic_sorted = sorted(episodic_indices, key=lambda i: -rrf_scores[i])
        knowledge_sorted = sorted(knowledge_indices, key=lambda i: -rrf_scores[i])
        user_sorted = sorted(user_indices, key=lambda i: -rrf_scores[i])
        decision_sorted = sorted(decision_indices, key=lambda i: -rrf_scores[i])
        summary_sorted = sorted(summary_indices, key=lambda i: -rrf_scores[i])

        # Select Top-K from each pool
        top_episodic = episodic_sorted[:TOP_K_EPISODIC]
        top_knowledge = knowledge_sorted[:TOP_K_KNOWLEDGE]
        top_user = user_sorted[:TOP_K_USER]
        top_decision = decision_sorted[:TOP_K_DECISION]
        
        top_summaries = []
        if use_summaries:
            top_summaries = summary_sorted[:TOP_K_SUMMARIES]

        # Phase 3 — Unified Merged-Pool Reranking (Jina v2 Base CE)
        #
        # All types are merged into one candidate pool and ranked together in a
        # single Jina v2 pass. From the sorted output:
        #   - The first HEAVY_TOP_K_ANCHORS decision memories become BM25-expansion anchors.
        #   - All remaining entries fill the general pool subject to per-type caps.
        # This ensures decision memories compete fairly on the same relevance scale
        # as episodic/knowledge/user memories — no separate rerank pass needed.

        merged_candidates = []

        def _make_memory_candidate(idx, entry, include_anchor_text=False):
            content_text = _get_content_only(entry)
            searchable = f"{entry.path} : {content_text}" if entry.path else content_text
            c = {
                "entry_type": "memory",
                "entry_key": f"mem:{entry.memory_type}:{entry.memory_id}",
                "memory_id": entry.memory_id,
                "memory_type": entry.memory_type,
                "topic_id": entry.topic_id,
                "name": entry.name,
                "path": entry.path,
                "level": entry.level,
                "timestamp": entry.timestamp,
                "message_id": entry.message_id,
                "searchable_text": _truncate_for_ce(searchable),
                "rrf_score": float(rrf_scores[idx]),
                "best_sub_query": ctx.query_text,
            }
            if include_anchor_text:
                c["anchor_query_text"] = content_text
            return c

        for idx in top_episodic:
            merged_candidates.append(_make_memory_candidate(idx, data.entries[idx]))
        for idx in top_knowledge:
            merged_candidates.append(_make_memory_candidate(idx, data.entries[idx]))
        for idx in top_user:
            merged_candidates.append(_make_memory_candidate(idx, data.entries[idx]))
        for idx in top_decision:
            # Decision entries carry anchor_query_text so Phase 5 BM25 expansion
            # can use the memory's own content as the anchor query.
            merged_candidates.append(_make_memory_candidate(idx, data.entries[idx], include_anchor_text=True))

        for idx in top_summaries:
            entry = data.entries[idx]
            content_text = _get_content_only(entry)
            searchable = f"{entry.path} : {content_text}" if entry.path else content_text
            merged_candidates.append({
                "entry_type": "node",
                "entry_key": f"node:{entry.topic_id}",
                "topic_id": entry.topic_id,
                "name": entry.name,
                "path": entry.path,
                "level": entry.level,
                "is_leaf": entry.is_leaf,
                "searchable_text": _truncate_for_ce(searchable),
                "rrf_score": float(rrf_scores[idx]),
                "best_sub_query": ctx.query_text,
            })

        # Single Jina v2 rerank over the full merged pool
        top3_decision_anchors = []
        fixed_tier_memories = []
        additional_tier_memories = []
        selected_general_memories = []
        selected_general_summaries = []
        # user starts at -3 to guarantee at least (JINA_FINAL_INDIVIDUAL+3)=8 user slots
        # before the global cap kicks in, preventing profile facts being crowded out.
        selected_individual = {"user": -3, "decision": 0, "knowledge": 0, "episodic": 0}

        if merged_candidates:
            reranked_merged = self.reranker.rerank(merged_candidates, top_k=len(merged_candidates))

            for c in reranked_merged:
                if c["entry_type"] == "memory":
                    mtype = c["memory_type"]
                    # Decision memories: greedily fill anchor slots first
                    if mtype == "decision" and len(top3_decision_anchors) < HEAVY_TOP_K_ANCHORS:
                        top3_decision_anchors.append(c)
                        # Anchors are excluded from the general pool entirely
                        continue
                    # General pool: per-type capped fixed tier → overflow additional tier
                    if selected_individual[mtype] < JINA_FINAL_INDIVIDUAL:
                        selected_individual[mtype] += 1
                        fixed_tier_memories.append(c)
                    else:
                        additional_tier_memories.append(c)
                elif c["entry_type"] == "node":
                    if len(selected_general_summaries) < JINA_FINAL_K_SUMMARIES:
                        selected_general_summaries.append(c)

            selected_general_memories = fixed_tier_memories + additional_tier_memories
            selected_general_memories = selected_general_memories[:top_k_corpus]
            
        session = self.Session()
        try:
            anchor_expansions = {}

            # Phase 5 — Pure BM25 Expansion (per decision anchor)
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]

                anchor_query_text = (anchor.get("anchor_query_text") or anchor.get("searchable_text") or "").strip()
                if not anchor_query_text:
                    anchor_expansions[anchor_key] = []
                    continue

                anchor_tokens = cache._tokenize(anchor_query_text)
                bm25_scores_anchor = cache.bm25_score_all(anchor_tokens)

                candidates = []
                for idx, entry in enumerate(data.entries):
                    if getattr(entry, "entry_type", "") == "memory" and entry.memory_type in ("knowledge", "episodic"):
                        score = bm25_scores_anchor[idx]
                        if score >= MIN_BM25_THRESHOLD:
                            candidates.append((idx, score))

                # Sort by BM25 score descending
                candidates.sort(key=lambda x: -x[1])
                
                # Take top 30 BM25 candidates (widened for better expansion recall)
                top_candidates = candidates[:20]

                expansion_entries = []
                for idx, score in top_candidates:
                    entry = data.entries[idx]
                    expansion_entries.append({
                        "entry_type": "memory",
                        "entry_key": f"mem:{entry.memory_type}:{entry.memory_id}",
                        "memory_id": entry.memory_id,
                        "memory_type": entry.memory_type,
                        "topic_id": entry.topic_id,
                        "name": entry.name,
                        "path": entry.path,
                        "level": entry.level,
                        "timestamp": entry.timestamp,
                        "message_id": entry.message_id,
                        "searchable_text": _truncate_for_ce(entry.searchable_text or ""),
                        "rrf_score": float(rrf_scores[idx]),
                        "bm25_score_for_anchor": float(score)
                    })

                anchor_expansions[anchor_key] = expansion_entries

            # Phase 6 — Expansion Reranking (Jina CE per anchor)
            selected_expansions_map = {}
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]
                candidates = anchor_expansions.get(anchor_key, [])
                if not candidates:
                    selected_expansions_map[anchor_key] = []
                    continue

                anchor_context = _truncate_for_ce(
                    anchor.get("anchor_query_text")
                    or anchor.get("searchable_text")
                )
                ce_query = f"{anchor_context}".strip()

                for c in candidates:
                    c["best_sub_query"] = ce_query

                reranked_expansion = self.reranker.rerank(candidates, top_k=len(candidates))
                
                # Select top expansions (10 for broad queries, 5 for specific queries)
                cutoff = 10 if qc.is_broad else 5
                selected_expansions_map[anchor_key] = reranked_expansion[:cutoff]

            # Phase 7 — Global Deduplication & Assembly
            # Initialize claimed keys set (using memory_id as globally unique key)
            claimed_keys = set()
            for m in selected_general_memories:
                claimed_keys.add(m["memory_id"])
            for m in top3_decision_anchors:
                claimed_keys.add(m["memory_id"])

            # Deduplicate selected expansions in anchor priority order
            deduped_expansions_map = {} # anchor_key -> list of deduped candidates
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]
                candidates = selected_expansions_map.get(anchor_key, [])
                
                deduped_candidates = []
                for c in candidates:
                    key = c["memory_id"]
                    if key not in claimed_keys:
                        deduped_candidates.append(c)
                        claimed_keys.add(key)
                deduped_expansions_map[anchor_key] = deduped_candidates

            # Phase 8 — Final Assembly & Formatting
            mem_ids_by_type = defaultdict(set)
            for m in top3_decision_anchors:
                mem_ids_by_type[m["memory_type"]].add(m["memory_id"])
            for m in selected_general_memories:
                mem_ids_by_type[m["memory_type"]].add(m["memory_id"])
            for cand_list in deduped_expansions_map.values():
                for c in cand_list:
                    mem_ids_by_type[c["memory_type"]].add(c["memory_id"])
            assembly_db_map = self._fetch_memories_batch(session, mem_ids_by_type)

            topic_ids = set()
            selected_summaries = selected_general_summaries
            if use_summaries:
                for c in selected_summaries:
                    topic_ids.add(c["topic_id"])
            topic_db_map = self._fetch_topics_batch(session, topic_ids)

            context_parts = []
            collected_leaf_ids = set()
            collected_branch_ids = set()

            # 8a. Format [USER PROFILE & STANDING PREFERENCES] block with nesting
            protected_parts = []
            
            # Format and nesting logic for active anchors
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]
                if not anchor["searchable_text"] or not anchor["path"]:
                    continue

                anchor_db = assembly_db_map.get(anchor["memory_id"])
                if not anchor_db or not anchor_db.content:
                    continue

                status = getattr(anchor_db, "status", "active")
                context = getattr(anchor_db, "context", "")
                header = f"[DECISION ({status})] {anchor['name']}Context Behind this decision : {context}"

                ts = anchor_db.timestamp.strftime("%Y-%m-%d %H:%M") if anchor_db.timestamp else None
                ts_line = f"[Recorded: {ts}]\n" if ts else ""

                anchor_text = f"{header}\n{ts_line}{anchor_db.content}"

                related_candidates = deduped_expansions_map.get(anchor_key, [])
                related_lines = []
                for rc in related_candidates:
                    mem_entry = assembly_db_map.get(rc["memory_id"])
                    if mem_entry and mem_entry.content:
                        rc_ts = mem_entry.timestamp.strftime("%Y-%m-%d %H:%M") if mem_entry.timestamp else None
                        rc_ts_line = f"[Recorded: {rc_ts}]\n" if rc_ts else ""
                        rc_label = rc["memory_type"].upper()
                        rc_header = f"  [Related - {rc_label}] {rc['name']}"
                        rc_body = f"{rc_ts_line}{mem_entry.content}"
                        indented_body = "\n".join(f"  {line}" for line in rc_body.splitlines())
                        related_lines.append(f"{rc_header}\n{indented_body}")

                if related_lines:
                    anchor_text = anchor_text + "\n" + "\n".join(related_lines)

                protected_parts.append(anchor_text)
                collected_leaf_ids.add(anchor["topic_id"])

            # Now add USER memories from general memories pool to protected parts
            for m in selected_general_memories:
                if m["memory_type"] == "user":
                    mem_entry = assembly_db_map.get(m["memory_id"])
                    if mem_entry and mem_entry.content:
                        header = f"[USER] {m['name']}"
                        ts = mem_entry.timestamp.strftime("%Y-%m-%d %H:%M") if mem_entry.timestamp else None
                        ts_line = f"[Recorded: {ts}]\n" if ts else ""
                        protected_parts.append(f"{header}\n{ts_line}{mem_entry.content}")
                        collected_leaf_ids.add(m["topic_id"])

            if protected_parts:
                context_parts.append(
                    "[USER PROFILE & STANDING PREFERENCES]\n"
                    + "\n\n".join(protected_parts)
                )

            # 8b. Format leaf summaries for broad queries
            if use_summaries:
                for c in selected_summaries:
                    if c.get("is_leaf"):
                        topic = topic_db_map.get(c["topic_id"])
                        if topic:
                            desc_text = (topic.description or "")
                            if desc_text.strip():
                                context_parts.append(
                                    f"[LEAF SUMMARY] {topic.name}\n"
                                    f"{desc_text.strip()}"
                                )
                                collected_leaf_ids.add(c["topic_id"])

            # 8c. Format standalone non-protected memories (knowledge/episodic/decision)
            standalone_memories = []
            for c in selected_general_memories:
                if c["memory_type"] in ("knowledge", "episodic", "decision"):
                    standalone_memories.append(c)

            memories_by_topic = defaultdict(list)
            for c in standalone_memories:
                memories_by_topic[c["topic_id"]].append(c)

            for topic_id, mems in sorted(memories_by_topic.items()):
                topic_name = mems[0]["name"]
                mems_by_type = defaultdict(list)
                for m in mems:
                    mems_by_type[m["memory_type"]].append(m)

                for mtype in ("knowledge", "episodic", "decision"):
                    type_mems = mems_by_type.get(mtype, [])
                    if not type_mems:
                        continue

                    label = mtype.upper()
                    lines = []
                    for m in type_mems:
                        mem_entry = assembly_db_map.get(m["memory_id"])
                        if mem_entry and mem_entry.content:
                            ts = mem_entry.timestamp.strftime("%Y-%m-%d %H:%M") if mem_entry.timestamp else None
                            ts_line = f"[Recorded: {ts}]\n" if ts else ""
                            lines.append(f"{ts_line}{mem_entry.content}")

                    if lines:
                        content_str = "\n".join(lines)
                        if label == "DECISION":
                            status = getattr(mem_entry, "status", "active") if mem_entry else "active"
                            header = f"[DECISION ({status})] {topic_name}"
                        else:
                            header = f"[{label}] {topic_name}"

                        context_parts.append(
                            f"{header}\n"
                            f"{content_str}"
                        )
                        collected_leaf_ids.add(topic_id)

            # 8d. Format individual branch summaries
            branch_candidates = [c for c in selected_summaries if not c.get("is_leaf")]
            individual_branches = []

            if use_summaries and branch_candidates:
                sibling_groups = {}
                for bc in branch_candidates:
                    topic = topic_db_map.get(bc["topic_id"])
                    if not topic:
                        continue
                    parent_id = topic.parent_id
                    if parent_id is not None:
                        sibling_groups.setdefault(parent_id, []).append((bc, topic))

                for group in sibling_groups.values():
                    for bc, topic in group:
                        if topic.level != 0:
                            individual_branches.append((bc, topic))

            for bc, topic in individual_branches:
                content = topic.description or ""
                if not content.strip():
                    continue
                collected_branch_ids.add(topic.id)
                header_tag  = "[BRANCH SUMMARY]"
                context_parts.append(
                    f"{header_tag} {topic.name}\n"
                    f"{content}\n"
                )

            # Gather all unique expanded memories for tracking
            selected_expansions = []
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]
                selected_expansions.extend(deduped_expansions_map.get(anchor_key, []))

            t3 = time.time()
            selected_all = top3_decision_anchors + selected_expansions + selected_general_memories + selected_general_summaries
            trace["selected_topic_ids"] = list(collected_leaf_ids) + list(collected_branch_ids)
            trace["selected_entry_keys"] = [s["entry_key"] for s in selected_all]

        finally:
            session.close()

        t4 = time.time()
        logger.info(f"[Retrieval] DB fetch: {t4-t2:.3f}s")

        full_context = "\n\n".join(context_parts)
        full_context = re.sub(r'\n{3,}', '\n\n', full_context)

        total = time.time() - t0
        n_mems = len(top3_decision_anchors) + len(selected_expansions) + len(selected_general_memories)
        n_sums = len(selected_general_summaries)
        logger.info(f"[Retrieval] TOTAL: {total:.2f}s | type={query_type} | {n_mems} memories + {n_sums} summaries = {n_mems+n_sums} context items | 0 LLM calls")

        system_prompt = get_system_prompt(qc.is_temporal, qc.is_broad)

        return RetrievalResult(
            context=full_context,
            trace=trace,
            system_hint=system_prompt,
            query_type=query_type,
        )

    def _fetch_memories_batch(self, session, memory_ids_by_type):
        results = {}
        mem_class_map = {
            "knowledge": KnowledgeMemory,
            "episodic": EpisodicMemory,
            "user": UserMemory,
            "decision": DecisionMemory,
        }
        for mtype, ids in memory_ids_by_type.items():
            if not ids:
                continue
            MemClass = mem_class_map.get(mtype)
            if MemClass:
                mems = session.query(MemClass).options(defer(MemClass.embedding)).filter(MemClass.id.in_(list(ids))).all()
                for m in mems:
                    results[m.id] = m
        return results

    def _fetch_topics_batch(self, session, topic_ids):
        results = {}
        if topic_ids:
            topics = session.query(Topic).options(defer(Topic.embedding)).filter(Topic.id.in_(list(topic_ids))).all()
            for t in topics:
                results[t.id] = t
        return results