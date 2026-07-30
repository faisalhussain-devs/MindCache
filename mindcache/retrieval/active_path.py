import re
import time
from collections import defaultdict
from sqlalchemy.orm import defer
import numpy as np

from mindcache.Database.embedder import EmbeddingManager
from mindcache.Database.db_setup import (
    Topic, EpisodicMemory,
    UserMemory, KnowledgeMemory, DecisionMemory, Session
)
from mindcache.retrieval.structs import RetrievalResult
from mindcache.retrieval.context_bridge import ContextBridge
from mindcache.retrieval.root_cache import CollapsedTreeCache, get_tree_cache
from mindcache.retrieval.hybrid_search import CrossEncoderManager


# --- New Partitioned Pipeline Constants ---
TOP_K_EPISODIC   = 40    # per-type RRF pool
TOP_K_KNOWLEDGE  = 40
TOP_K_USER       = 30    # widened to ensure short user-profile memories enter Jina pool
TOP_K_DECISION   = 30
TOP_K_SUMMARIES  = 10    # broad queries only

JINA_FINAL_K_MEMORIES  = 30   # top memories out of merged Jina pool
JINA_FINAL_K_SUMMARIES = 3    # top summaries out of merged Jina pool
JINA_FINAL_INDIVIDUAL = 5  # per-type cap
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

_BROAD_PHRASES = [
    "summarize", "summary", "compare",         # "summaries" removed (fires on "chapter summaries" noun)
    "how my understanding", "how did my", "evolved", "developed",
    "across sessions", "all about", "give me an overview", "broad",
    "explain", "best way to",                  # "how do i", "how would you" removed (procedural/preference lookup)
    "show me how", "how does", "what are all", "tell me about",
    "describe", "walk me through", "what happened with",
    "timeline", "reconstruct", "chronolog",    # "overview" removed (fires on document names e.g. "design overview")
]


from dataclasses import dataclass
import logging
logger = logging.getLogger(__name__)

@dataclass
class QueryClassification:
    label: str
    is_broad: bool

def classify_query(query: str) -> QueryClassification:
    """
    Classify the query along one dimension:
    Broad/Synthesis vs Information Extraction/Fact (checked via _BROAD_PHRASES).

    Returns a QueryClassification with label 'broad_overview' or
    'information_extraction' and a boolean is_broad flag.
    """
    q = query.lower()
    is_broad = any(p in q for p in _BROAD_PHRASES)
    val = "broad_overview" if is_broad else "information_extraction"
    return QueryClassification(val, is_broad)


_UNIFIED_SYSTEM_PROMPT = """<system>

<role>

You are a memory-grounded synthesis engine.
Your only knowledge source is the retrieved context.
Do not use outside knowledge.
Do not infer missing facts.
If the retrieved context cannot answer the question, explicitly state what information is missing.

</role>

<execution_pipeline>

<step id="1" name="Understand Query">
Determine the question type before answering.
Possible categories include:
- Fact lookup
- Broad summary / evolution
- Timeline
- Recommendation
- Comparison
- Arithmetic / counting
- Event-specific lookup
- Contradiction resolution
Only activate the reasoning required for that question type.
</step>

<step id="2" name="Collect Evidence">
Read the entire retrieved context.
Do not stop after finding the first relevant memory.
Identify every memory relevant to the user's question.
Ignore unrelated memories.
When multiple memories discuss the same entity, event, decision or attribute, collect all of them before reasoning.
If this is a broad summary question:
1. Read branch summaries first to identify the overall structure.
2. Then read the supporting memories.
3. Replace generic statements from summaries with the specific people, places, decisions, events and facts contained in the supporting memories.
Branch summaries are organizational guides.
Supporting memories are the evidence.
</step>

<step id="3" name="Reason">
Apply only the reasoning required.
<memory_authority>
When multiple memory types describe the same user state, use the following authority unless explicit evidence indicates otherwise:

1. USER memories
   - Direct statements made by the user.
   - Highest authority for the user's current state, preferences and facts.

2. DECISION memories
   - Active decisions and commitments.
   - Override earlier intentions but do not override later USER statements.

3. KNOWLEDGE memories
   - Derived explanations, calculations or synthesized facts.
   - Use these unless they conflict with a direct USER memory or an active DECISION.

4. EPISODIC memories
   - Conversation history and context.
   - Use primarily to reconstruct events and timelines rather than current state.
Do not allow a synthesized KNOWLEDGE memory to override a direct USER memory unless the retrieved context explicitly states that it supersedes or updates it.


Origin metadata is authoritative:
- `origin=user-stated` or `origin=user-confirmed` is direct evidence of the user's state.
- `origin=assistant-advice` and `origin=assistant-statement` record what the assistant said or recommended. They are never evidence that the user believes, chose, completed, or prefers that content.
- `origin=conversation-synthesis` records a user-specific exchange or discussion context. Use it as context, not as proof of a completed user action unless the content explicitly says so.
- `origin=legacy-unknown` lacks structured provenance. Treat it cautiously and never let it override direct user-stated or user-confirmed evidence.

</memory_authority>

<timeline>

Order events chronologically.
When describing evolution, present events from oldest to newest.
Do not assume the newest memory is automatically correct.
Determine whether a later memory:

- updates an earlier fact,
- corrects an earlier fact,
- supersedes an earlier value,
- continues the same event,
- or represents a separate scenario.

If a later memory explicitly replaces an earlier value, answer using the updated value.
If memories genuinely conflict and neither clearly supersedes the other, report both and explicitly mention the conflict.
If a memory preserves a relative duration or ambiguous wording, keep that wording visible instead of converting it into a precise fact without support.
</timeline>

<arithmetic>

Arithmetic is the final fallback, not the primary reasoning method.
Before performing any calculation:
1. Search the retrieved memories for an explicit calculated result.
2. If an explicit result already answers the question, always use that value.
3. Do not recompute totals that already exist.
4. Only perform arithmetic if no explicit result exists anywhere in the retrieved context.
Manual arithmetic should be avoided whenever possible because retrieved context may contain many related memories, and omitting even one relevant memory can produce an incorrect calculation.

</arithmetic>


<recommendation>
Respect active user decisions and standing preferences.
Do not recommend options that contradict active decisions unless the user explicitly asks to reconsider them.
</recommendation>


<contradictions>
If multiple memories describe the same fact:
First determine whether they are:
- duplicate memories,
- historical updates,
- corrections,
- evolving states,
- or genuine contradictions.

Treat updates as evolution rather than conflicts.
Treat corrections as replacing the incorrect value.
Only report a contradiction when multiple memories cannot both be true and no retrieved evidence resolves the conflict.
</contradictions>
<context_selection>

If multiple retrieved memories represent different financial states, plans, hypothetical scenarios or time periods:
Do not combine them automatically.
First determine which memories belong to the same state or scenario.
Prefer memories explicitly describing the user's current state over historical examples, hypothetical calculations or planning discussions.
If multiple equally valid interpretations remain and the retrieved context does not resolve them, explicitly explain the ambiguity instead of selecting one arbitrarily.
</context_selection>


<event_lookup>
Only answer with details explicitly connected to the requested event.
If the connection is missing, state exactly what information is unavailable.
Do not substitute generic information.
</event_lookup>

</step>

<step id="4" name="Compose">
Generate the answer only from collected evidence.
Prefer concrete facts over abstract summaries.
Prefer retrieved evidence over paraphrased generalizations.
Do not invent relationships between memories that are not explicitly supported.
</step>

<step id="5" name="Validation">
Before returning the answer, silently verify:
✓ Did I answer every part of the question?
✓ Did I read every relevant retrieved memory?
✓ For broad summaries, did I use summaries only as structure and supporting memories as evidence?
✓ Did I miss any explicit numbers, dates or decisions?
✓ Did I reuse an explicit calculated value instead of recomputing it?
✓ Did I distinguish updates from genuine contradictions?
✓ Did I preserve conflicting or time-relative evidence written in memory content?
✓ Did I accidentally merge multiple scenarios into one answer?
✓ Did I ignore a later memory that explicitly superseded an earlier one?
✓ Did I introduce information not supported by the retrieved context?
If any answer is "Yes", correct the response before returning it.

</step>

</execution_pipeline>

<response>

Be concise.
Be precise.
Use only retrieved evidence.
If information is missing, explicitly state what is missing rather than guessing.

</response>

</system>
"""

def get_system_prompt(is_broad: bool) -> str:
    return _UNIFIED_SYSTEM_PROMPT


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
        include_summaries: bool = False,
        use_reranker: bool = True,
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
        rrf_scores = (0.7 * v_scores + 0.3 * b_scores).astype(np.float32)

        t2 = time.time()
        logger.info(f"[Retrieval] BM25+Vec+RRF on {N} nodes: {t2-t1:.3f}s")

        # Classify query
        qc = classify_query(ctx.query_text)
        query_type = qc.label
        use_summaries = qc.is_broad

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
        selected_individual = {"user": -3, "decision": 0, "knowledge": 0, "episodic": 0}

        if merged_candidates:
            if use_reranker:
                reranked_merged = self.reranker.rerank(merged_candidates, top_k=len(merged_candidates))
            else:
                # No reranker: sort by RRF score descending as a fallback
                reranked_merged = sorted(merged_candidates, key=lambda c: c.get("rrf_score", 0), reverse=True)
                logger.info("[Retrieval] Phase 3: reranker disabled, using RRF ordering.")

            for c in reranked_merged:
                if c["entry_type"] == "memory":
                    mtype = c["memory_type"]
                    if mtype == "decision" and len(top3_decision_anchors) < HEAVY_TOP_K_ANCHORS:
                        top3_decision_anchors.append(c)
                        continue
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
                
                # Take top 20 BM25 candidates (widened for better expansion recall)
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

                if use_reranker:
                    reranked_expansion = self.reranker.rerank(candidates, top_k=len(candidates))
                else:
                    reranked_expansion = sorted(candidates, key=lambda c: c.get("rrf_score", 0), reverse=True)
                    logger.info("[Retrieval] Phase 6: reranker disabled, using RRF ordering.")

                # Select top expansions (10 for broad queries, 5 for specific queries)
                cutoff = 5
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
            assembly_db_map = self._fetch_memories_batch(session, mem_ids_by_type, user_id=user_id)

            topic_ids = set()
            selected_summaries = selected_general_summaries
            if use_summaries:
                for c in selected_summaries:
                    topic_ids.add(c["topic_id"])
            topic_db_map = self._fetch_topics_batch(session, topic_ids, user_id=user_id)

            memory_parts_dict = {
                "DECISION": [],
                "EPISODIC": [],
                "KNOWLEDGE": [],
                "USER": []
            }
            summary_parts = []
            collected_leaf_ids = set()
            collected_branch_ids = set()

            # 8a. Format decision anchors with related expansion memories
            for anchor in top3_decision_anchors:
                anchor_key = anchor["memory_id"]
                if not anchor["searchable_text"] or not anchor["path"]:
                    continue

                anchor_db = assembly_db_map.get(anchor["memory_id"])
                if not anchor_db or not anchor_db.content:
                    continue

                decision_context = getattr(anchor_db, "context", "")
                ts = anchor_db.timestamp.strftime("%Y-%m-%d") if anchor_db.timestamp else None
                date_attr = f' date="{ts}"' if ts else ""

                related_candidates = deduped_expansions_map.get(anchor_key, [])
                ref_ids = [str(rc["memory_id"]) for rc in related_candidates if assembly_db_map.get(rc["memory_id"])]
                refs_attr = f' refs="{",".join(ref_ids)}"' if ref_ids else ""

                mem_lines = [f'<memory_item id="{anchor["memory_id"]}" type="DECISION"{date_attr}{refs_attr}>']
                mem_lines.append(f"- Decision: {anchor_db.content}")
                if decision_context:
                    mem_lines.append(f"- Context: {decision_context}")
                mem_lines.append("</memory_item>")
                memory_parts_dict["DECISION"].append("\n".join(mem_lines))
                collected_leaf_ids.add(anchor["topic_id"])

                # Add related memories to flat tags
                for rc in related_candidates:
                    mem_entry = assembly_db_map.get(rc["memory_id"])
                    if mem_entry and mem_entry.content:
                        rc_ts = mem_entry.timestamp.strftime("%Y-%m-%d") if mem_entry.timestamp else None
                        rc_date_attr = f' date="{rc_ts}"' if rc_ts else ""
                        rc_label = rc["memory_type"].upper()
                        if rc_label not in memory_parts_dict:
                            memory_parts_dict[rc_label] = []
                        memory_parts_dict[rc_label].append(
                            f'<memory_item id="{rc["memory_id"]}" type="{rc_label}"{rc_date_attr}>\n'
                            f'- Content: {mem_entry.content}\n'
                            f'</memory_item>'
                        )

            # 8b. Format USER memories
            for m in selected_general_memories:
                if m["memory_type"] == "user":
                    mem_entry = assembly_db_map.get(m["memory_id"])
                    if mem_entry and mem_entry.content:
                        ts = mem_entry.timestamp.strftime("%Y-%m-%d") if mem_entry.timestamp else None
                        date_attr = f' date="{ts}"' if ts else ""
                        memory_parts_dict["USER"].append(
                            f'<memory_item id="{m["memory_id"]}" type="USER"{date_attr}>\n'
                            f'- Content: {mem_entry.content}\n'
                            f'</memory_item>'
                        )
                        collected_leaf_ids.add(m["topic_id"])

            # 8c. Format leaf summaries for broad queries
            if use_summaries:
                for c in selected_summaries:
                    if c.get("is_leaf"):
                        topic = topic_db_map.get(c["topic_id"])
                        if topic:
                            desc_text = (topic.description or "")
                            if desc_text.strip():
                                topic_attr = f' topic="{topic.name}"' if topic.name else ""
                                summary_parts.append(
                                    f'<leaf_summary{topic_attr}>\n'
                                    f'{desc_text.strip()}\n'
                                    f'</leaf_summary>'
                                )
                                collected_leaf_ids.add(c["topic_id"])

            # 8d. Format standalone memories (knowledge/episodic/decision)
            for c in selected_general_memories:
                if c["memory_type"] not in ("knowledge", "episodic", "decision"):
                    continue
                mem_entry = assembly_db_map.get(c["memory_id"])
                if not mem_entry or not mem_entry.content:
                    continue

                label = c["memory_type"].upper()
                ts = mem_entry.timestamp.strftime("%Y-%m-%d") if mem_entry.timestamp else None
                date_attr = f' date="{ts}"' if ts else ""

                if label not in memory_parts_dict:
                    memory_parts_dict[label] = []

                if label == "DECISION":
                    decision_context = getattr(mem_entry, "context", "")
                    mem_lines = [f'<memory_item id="{c["memory_id"]}" type="DECISION"{date_attr}>']
                    mem_lines.append(f"- Decision: {mem_entry.content}")
                    if decision_context:
                        mem_lines.append(f"- Context: {decision_context}")
                    mem_lines.append("</memory_item>")
                    memory_parts_dict["DECISION"].append("\n".join(mem_lines))
                else:
                    memory_parts_dict[label].append(
                        f'<memory_item id="{c["memory_id"]}" type="{label}"{date_attr}>\n'
                        f'- Content: {mem_entry.content}\n'
                        f'</memory_item>'
                    )
                collected_leaf_ids.add(c["topic_id"])

            # 8e. Format individual branch summaries
            branch_candidates = [c for c in selected_summaries if not c.get("is_leaf")]

            if use_summaries and branch_candidates:
                for bc in branch_candidates:
                    topic = topic_db_map.get(bc["topic_id"])
                    if not topic:
                        continue
                    content = topic.description or ""
                    if not content.strip():
                        continue
                    collected_branch_ids.add(topic.id)
                    topic_attr = f' topic="{topic.name}"' if topic.name else ""
                    summary_parts.append(
                        f'<branch_summary{topic_attr}>\n'
                        f'{content.strip()}\n'
                        f'</branch_summary>'
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

        final_blocks = []
        if summary_parts:
            final_blocks.append("<branch_summaries>\n" + "\n".join(summary_parts) + "\n</branch_summaries>")
        
        has_memories = any(memory_parts_dict.values())
        if has_memories:
            final_blocks.append("<retrieved_memories>\n")
            # Enforce a consistent order
            for mtype in ["DECISION", "USER", "KNOWLEDGE", "EPISODIC"]:
                parts = memory_parts_dict.get(mtype, [])
                if parts:
                    tag_name = f"{mtype.lower()}_memories"
                    final_blocks.append(f"<{tag_name}>\n" + "\n".join(parts) + f"\n</{tag_name}>\n")
            final_blocks.append("</retrieved_memories>")
        
        full_context = "\n".join(final_blocks)
        full_context = re.sub(r'\n{3,}', '\n\n', full_context)

        total = time.time() - t0
        n_mems = len(top3_decision_anchors) + len(selected_expansions) + len(selected_general_memories)
        n_sums = len(selected_general_summaries)
        logger.info(f"[Retrieval] TOTAL: {total:.2f}s | type={query_type} | {n_mems} memories + {n_sums} summaries = {n_mems+n_sums} context items | 0 LLM calls")

        system_prompt = get_system_prompt(qc.is_broad)

        return RetrievalResult(
            context=full_context,
            trace=trace,
            system_hint=system_prompt,
            query_type=query_type,
        )

    def _fetch_memories_batch(self, session, memory_ids_by_type, user_id=None):
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
                query = session.query(MemClass).options(defer(MemClass.embedding)).filter(MemClass.id.in_(list(ids)))
                if user_id:
                    query = query.filter(MemClass.user_id == user_id)
                mems = query.all()
                for m in mems:
                    results[m.id] = m
        return results

    def _fetch_topics_batch(self, session, topic_ids, user_id=None):
        results = {}
        if topic_ids:
            query = session.query(Topic).options(defer(Topic.embedding)).filter(Topic.id.in_(list(topic_ids)))
            if user_id:
                query = query.filter(Topic.user_id == user_id)
            topics = query.all()
            for t in topics:
                results[t.id] = t
        return results
