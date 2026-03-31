import math
from collections import Counter
from Database.db_setup import Topic
from Database.db_manager import DatabaseManager
from retrieval.structs import RetrievalContext, RetrievalConfig, CandidateTopic
from retrieval.context_bridge import ContextBridge
import re
from api_server import get_tree_cache

class BM25Scorer:
    """Lightweight BM25 scorer for topic name + description."""
    
    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.corpus = []      # List of tokenized docs
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
        
        # Calculate IDF
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
        self.db_manager = DatabaseManager()
        self.Session = self.db_manager.Session

    def descend(
        self,
        start_nodes: list[Topic],
        ctx: RetrievalContext,
        include_start_nodes: bool = False,
        force_full_subtree: bool = False,
    ) -> list[CandidateTopic]:
        """
        Phase 3: Lean Descent.
        Returns top-k candidates combined from multiple starting nodes.
        No descriptions or summaries sent to refiner.
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

                # Collect matching nodes recursively from this starting point
                self._recursive_collect(
                    node=node,
                    query_vec=ctx.query_vector,
                    candidates=all_candidates,
                    current_path=current_path,
                    seen_ids=seen_ids,
                    force_full_subtree=force_full_subtree,
                )

            if not all_candidates:
                return []

            # BM25 scoring using chain path text for keyword matching
            bm25 = BM25Scorer()
            docs = [c['description'] for c in all_candidates]
            bm25.fit(docs)
            
            for i, c in enumerate(all_candidates):
                raw_bm25 = bm25.score(ctx.query_text, i)
                c['bm25_score'] = min(raw_bm25 / 15.0, 1.0)  # Bounded normalization
                c['combined'] = (c['sim_score'] * 0.8) + (c['bm25_score'] * 0.2)

            # Sort by combined score, take top-k
            all_candidates.sort(key=lambda x: x['combined'], reverse=True)
            top_k = all_candidates[:self.config.top_k]

            # Convert to CandidateTopic (strip description — only name+scores go to refiner)
            results = []
            for c in top_k:
                results.append(CandidateTopic(
                    name=c['name'],
                    path=c['path'],
                    topic_id=c['topic_id'],
                    sim_score=round(c['sim_score'], 3),
                    bm25_score=round(c['bm25_score'], 3),
                    timestamp=c['timestamp'],
                    is_leaf=c['is_leaf']
                ))
            
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
        if node.embedding is not None and query_vec is not None:
            node_vec = self.db_manager._from_blob(node.embedding)
            sim_score = self.bridge._cosine_similarity(query_vec, node_vec)
        elif not force_include:
            return False

        if selected_boost:
            sim_score = max(sim_score, 1.0)

        if not force_include and sim_score < self.config.descent_threshold:
            return False

        path_str = " > ".join(current_path)
        ts = node.timestamp.strftime('%Y-%m-%d %H:%M') if node.timestamp else "?"
        is_leaf = not bool(tree.children_map.get(node.id))

        candidates.append({
            'name': node.name,
            'path': path_str,
            'topic_id': node.id,
            'sim_score': float(sim_score),
            'bm25_score': 0.0,
            'description': f"{path_str} {node.description or ''}".strip(),
            'timestamp': ts,
            'is_leaf': is_leaf
        })
        seen_ids.add(node.id)
        return True

    def _recursive_collect(self, node, query_vec, candidates, current_path, seen_ids, force_full_subtree=False):
        """Recursively collect scored candidates from the topic tree."""
        tree = get_tree_cache()
        children = tree.children_map.get(node.id)
        if not children:
            return
        for child in children:
            child_path = current_path + [child.name]
            added = self._append_candidate(
                node=child,
                query_vec=query_vec,
                candidates=candidates,
                current_path=child_path,
                seen_ids=seen_ids,
                force_include=force_full_subtree,
            )

            if force_full_subtree or added:
                self._recursive_collect(
                    child,
                    query_vec,
                    candidates,
                    child_path,
                    seen_ids,
                    force_full_subtree=force_full_subtree,
                )
