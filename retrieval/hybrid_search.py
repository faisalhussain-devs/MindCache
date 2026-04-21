import math
import re
from collections import Counter

class BM25Scorer:
    """Lightweight BM25 scorer for leaf summaries/search texts."""
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = []
        self.doc_lens = []
        self.avg_dl = 0
        self.idf = {}
        self.N = 0

    def fit(self, documents: list[str]):
        self.corpus = [self._tokenize(doc) for doc in documents]
        self.N = len(self.corpus)
        if self.N == 0:
            return
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
        if self.N == 0:
            return 0.0
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

def calculate_rrf(vector_rank: int, bm25_rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion (RRF)"""
    v_score = 1.0 / (k + vector_rank) if vector_rank > 0 else 0.0
    b_score = 1.0 / (k + bm25_rank) if bm25_rank > 0 else 0.0
    return v_score + b_score

class CrossEncoderReranker:
    """
    Reranks candidates directly comparing the user_query against the candidate description.
    """
    def __init__(self, model_name: str = 'cross-encoder/ms-marco-MiniLM-L-6-v2'):
        try:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name, max_length=512)
            self.enabled = True
        except ImportError:
            print("[CrossEncoder] sentence-transformers not installed. Reranking disabled.")
            self.enabled = False

    def rerank(self, candidates: list[dict], top_k: int) -> list[dict]:
        """
        Reranks a list of candidate dictionaries. 
        Requires each candidate to have 'searchable_text' and 'best_sub_query'.
        Returns the top_k sorted candidates.
        """
        if not self.enabled or not candidates:
            return candidates[:top_k]

        # Cross encoder requires pairs of (query, document)
        pairs = [[c.get("best_sub_query", ""), c.get("searchable_text", "")] for c in candidates]
        
        try:
            scores = self.model.predict(pairs)
            for idx, score in enumerate(scores):
                candidates[idx]["cross_encoder_score"] = float(score)

            # Sort descending by cross-encoder score
            candidates.sort(key=lambda x: x.get("cross_encoder_score", 0.0), reverse=True)
            return candidates[:top_k]
            
        except Exception as e:
            print(f"[CrossEncoder] Reranking failed: {e}")
            return candidates[:top_k]

