import pickle
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Union
from collections import defaultdict
from functools import lru_cache
import logging
logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "collapsed_tree_cache.pkl"
EMB_MATRIX_FILE = CACHE_FILE.parent / "collapsed_tree_matrix.npy"
CE_TEXT_MAX_WORDS = 200

_spacy_nlp = None


def _get_spacy_nlp():
    global _spacy_nlp
    if _spacy_nlp is None:
        import spacy
        _spacy_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    return _spacy_nlp


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


@dataclass
class NodeMeta:
    """Represents a branch or leaf topic node in the retrieval pool."""
    topic_id: int
    name: str
    level: int
    path: str
    is_leaf: bool
    desc_words: int
    searchable_text: str
    entry_type: str = "node"


@dataclass
class MemoryMeta:
    """Represents a single individual memory row in the retrieval pool."""
    memory_id:    int
    memory_type:  str           # "knowledge" | "episodic" | "user" | "decision"
    topic_id:     int
    name:         str           # topic name (for display header in context)
    level:        int
    path:         str
    is_leaf:      bool = True
    timestamp:    Optional[str] = None
    message_id:   Optional[int] = None
    searchable_text: str = ""
    entry_type:   str = "memory"


CacheEntry = Union[NodeMeta, MemoryMeta]

class UnionFind:
    def __init__(self):
        self.parent = {}
        self.size = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.size[x] = 1
            return x
        # Path compression
        path = []
        curr = x
        while self.parent[curr] != curr:
            path.append(curr)
            curr = self.parent[curr]
        for node in path:
            self.parent[node] = curr
        return curr

    def union(self, x, y):
        root_x = self.find(x)
        root_y = self.find(y)
        if root_x != root_y:
            # Union by size: smaller family points to bigger, preserving balance
            if self.size[root_x] < self.size[root_y]:
                self.parent[root_x] = root_y
                self.size[root_y] += self.size[root_x]
                return root_x, root_y  # obsolete, surviving
            else:
                self.parent[root_y] = root_x
                self.size[root_x] += self.size[root_y]
                return root_y, root_x  # obsolete, surviving
        return None

    def to_dicts(self) -> tuple[dict, dict]:
        """
        Serialize the UnionFind as two plain Python dicts.
        Returns (parent, size) — both are safe to pickle and survive class changes.
        """
        return dict(self.parent), dict(self.size)

    @classmethod
    def from_dicts(cls, parent: dict, size: dict) -> "UnionFind":
        """
        Reconstruct a UnionFind from the two serialized dicts.
        The reconstructed object is identical in behaviour to the original.
        """
        uf = cls()
        uf.parent = parent
        uf.size = size
        return uf


@dataclass
class CollapsedTreeCacheData:
    entries: list = field(default_factory=list)
    inverted_index: dict = field(default_factory=dict)  # term -> {doc_idx: term_frequency}
    doc_lengths: list = field(default_factory=list)     # doc_idx -> length
    idf: dict = field(default_factory=dict)             # term -> idf
    avg_dl: float = 0.0
    corpus_size: int = 0
    union_find: UnionFind = field(default_factory=UnionFind)

    # Embedding matrix: shape (N, dim), row i corresponds to entries[i]
    embedding_matrix: Optional[np.ndarray] = None

    # Mapping from entry key to pool index
    entry_key_to_index: dict = field(default_factory=dict)

@dataclass
class Tree:
    children_map: dict = field(default_factory=dict)
    topic_by_id: dict = field(default_factory=dict)
    parent_map: dict = field(default_factory=dict)

    def __init__(self, session, user_id="default"):
        from mindcache.Database.db_setup import Topic, to_numpy
        self.children_map = defaultdict(list)
        self.parent_map = defaultdict(list)
        self.topic_by_id = {None: None}
        self.embedding_cache = {}
        # Fetch only topics belonging to this user
        topics = session.query(Topic).filter(Topic.user_id == user_id).all()
        for t in topics:
            self.topic_by_id[t.id] = t
            self.children_map[t.parent_id].append(t)
            if t.embedding:
                self.embedding_cache[t.id] = to_numpy(t.embedding).copy()
        self.parent_map = {t.id: self.topic_by_id.get(t.parent_id) for t in topics}

@lru_cache()
def get_tree_cache(user_id="default"):
    from mindcache.Database.db_setup import Session
    session = Session()
    try:
        return Tree(session, user_id=user_id)
    finally:
        session.close()

def refresh_tree_cache(user_id="default"):
    """
    Clear the tree cache AND the root leaf cache.
    Call this instead of get_tree_cache.cache_clear() directly
    so both caches stay in sync.
    """
    get_tree_cache.cache_clear()
    CollapsedTreeCache(user_id=user_id).clear()


from collections import OrderedDict
import threading

_COLLAPSED_CACHE_LOCK = threading.Lock()
_COLLAPSED_CACHE_REGISTRY = OrderedDict()
_COLLAPSED_CACHE_MAX_SIZE = 16

def _get_cached_collapsed_data(user_id: str) -> Optional["CollapsedTreeCacheData"]:
    with _COLLAPSED_CACHE_LOCK:
        if user_id in _COLLAPSED_CACHE_REGISTRY:
            # Move to end (most recently used)
            data = _COLLAPSED_CACHE_REGISTRY.pop(user_id)
            _COLLAPSED_CACHE_REGISTRY[user_id] = data
            return data
    return None

def _set_cached_collapsed_data(user_id: str, data: "CollapsedTreeCacheData") -> None:
    with _COLLAPSED_CACHE_LOCK:
        if user_id in _COLLAPSED_CACHE_REGISTRY:
            _COLLAPSED_CACHE_REGISTRY.pop(user_id)
        _COLLAPSED_CACHE_REGISTRY[user_id] = data
        if len(_COLLAPSED_CACHE_REGISTRY) > _COLLAPSED_CACHE_MAX_SIZE:
            _COLLAPSED_CACHE_REGISTRY.popitem(last=False)

def _clear_cached_collapsed_data(user_id: str) -> None:
    with _COLLAPSED_CACHE_LOCK:
        if user_id in _COLLAPSED_CACHE_REGISTRY:
            _COLLAPSED_CACHE_REGISTRY.pop(user_id)


def embed_and_save(db_session, record, text: str) -> Optional[np.ndarray]:
    """Helper function to generate an embedding for the given text and save it to the DB record."""
    if not text:
        return None
    try:
        from mindcache.Database.embedder import get_embedder
        embedder = get_embedder()
        vecs = embedder.get_batch_embeddings([text])
        if vecs is not None and len(vecs) > 0:
            vec = vecs[0]
            record.embedding = embedder._to_blob(vec)
            db_session.add(record)
            db_session.commit()
            return vec
    except Exception as e:
        db_session.rollback()
        logger.error(f"[CollapsedTreeCache] Failed to generate and save embedding: {e}")
    return None


class CollapsedTreeCache:
    """
    Module-level singleton. Replaces the old RootLeafCache.
    All nodes (leaves + branches) and individual memory rows are searchable
    in one flat pool. Each memory row has its own vector from its DB column.
    """

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self._data: Optional[CollapsedTreeCacheData] = None

    @property
    def cache_file(self) -> Path:
        base_path = Path(__file__).resolve().parent.parent / "cache"
        return base_path / f"collapsed_tree_cache_{self.user_id}.pkl"

    @property
    def emb_matrix_file(self) -> Path:
        base_path = Path(__file__).resolve().parent.parent / "cache"
        return base_path / f"collapsed_tree_matrix_{self.user_id}.npy"

    @property
    def is_ready(self) -> bool:
        d = self.data
        return d is not None and len(d.entries) > 0

    @property
    def data(self) -> Optional[CollapsedTreeCacheData]:
        if self._data is None:
            self._data = _get_cached_collapsed_data(self.user_id)
        return self._data

    @staticmethod
    def _normalize_and_lemmatize(text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        import string
        import unicodedata

        # Remove system tags [CURRENT], [PREV], [PREV2] if present
        for tag in ["[CURRENT]", "[PREV]", "[PREV2]"]:
            text = text.replace(tag, "")

        # 1. Lowercase and NFC normalize
        norm_text = unicodedata.normalize('NFC', text.lower())

        # 2. Use spaCy for lemmatization
        nlp = _get_spacy_nlp()
        doc = nlp(norm_text)

        # 3. Filter out punctuation and whitespace
        pairs = []
        for token in doc:
            if token.is_space:
                continue
            if token.is_punct:
                continue
            t_text = token.text
            if all(c in string.punctuation for c in t_text):
                continue
            raw = t_text
            lemma = token.lemma_.lower()
            pairs.append((raw, lemma))

        return pairs


    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, str]]:
        """Simple whitespace tokenizer that handles NFC normalization and lemmatization, returning (raw, lemma) pairs."""
        return CollapsedTreeCache._normalize_and_lemmatize(text)

    @staticmethod
    def _compute_bm25_idf(inverted_index: dict, corpus_size: int) -> dict:
        """Compute BM25 IDF for all terms in the inverted index at build time."""
        import math
        idf = {}
        for term, postings in inverted_index.items():
            df = len(postings)
            idf[term] = math.log(1 + (corpus_size - df + 0.5) / (df + 0.5))
        return idf

    def bm25_score_all(self, query_tokens: list, k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        """
        Score all nodes against query_tokens (pairs of raw and lemma) using pre-computed Inverted Index.
        Returns np.ndarray of shape (N,) with float scores.
        """
        import math
        d = self.data
        scores = np.zeros(d.corpus_size, dtype=np.float32)
        if not query_tokens:
            return scores

        for raw, lemma in query_tokens:
            family_raw = d.union_find.find(raw)
            family_lemma = d.union_find.find(lemma)

            if family_raw == family_lemma:
                term = family_raw
                if term not in d.idf or term not in d.inverted_index:
                    continue
                idf_val = d.idf[term]
                postings = d.inverted_index[term]
            else:
                # Merge posting lists in memory without modifying the persistent index
                merged_postings = {}
                if family_raw in d.inverted_index:
                    for doc_idx, tf in d.inverted_index[family_raw].items():
                        merged_postings[doc_idx] = merged_postings.get(doc_idx, 0) + tf
                if family_lemma in d.inverted_index:
                    for doc_idx, tf in d.inverted_index[family_lemma].items():
                        merged_postings[doc_idx] = merged_postings.get(doc_idx, 0) + tf

                if not merged_postings:
                    continue

                merged_df = len(merged_postings)
                idf_val = math.log(1 + (d.corpus_size - merged_df + 0.5) / (merged_df + 0.5))
                postings = merged_postings

            for doc_idx, tf in postings.items():
                dl = d.doc_lengths[doc_idx]
                norm = 1 - b + b * dl / d.avg_dl if d.avg_dl > 0 else 1.0
                scores[doc_idx] += idf_val * (tf * (k1 + 1)) / (tf + k1 * norm)
        return scores

    def vector_score_all(self, query_vec: np.ndarray) -> np.ndarray:
        """
        Cosine similarity between query_vec and all node embeddings.
        The embedding_matrix is pre-normalized at build/ingest time,
        so this is a single matrix-vector dot product — no per-query normalization.
        Returns np.ndarray of shape (N,).
        """
        d = self.data
        if d.embedding_matrix is None:
            return np.zeros(len(d.entries), dtype=np.float32)

        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return np.zeros(len(d.entries), dtype=np.float32)
        q = q / q_norm

        # Matrix is already row-normalized — pure dot product, no extra work.
        scores = d.embedding_matrix @ q
        return scores.astype(np.float32)

    def build_all(self, tree, include_summaries: bool = False) -> None:
        """
        Build the complete flat pool:
          - Branch topic nodes (NodeMeta) — searchable by description
          - Leaf topic nodes with LLM descriptions (NodeMeta)
          - Individual memory rows (MemoryMeta) — one entry per memory row,
            each with its own embedding loaded from the DB column.

        Computes BM25 pre-tokenization + IDF + embedding matrix.
        Persists to disk.
        """
        # Fetch all memory rows in one pass
        from mindcache.Database.db_setup import Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory, to_numpy, Session
        db_session = Session()

        # Run database repair for detached memories before building cache
        try:
            from mindcache.Database.repair_detached_memories import repair_detached_memories_for_user
            repair_detached_memories_for_user(db_session, self.user_id)
        except Exception as repair_err:
            logger.error(f"[CollapsedTreeCache] Failed to run database repair before building cache: {repair_err}")

        try:
            from mindcache.Database.embedder import run_embedding_job, run_memory_embedding_job

            has_missing_topics = db_session.query(Topic).filter(
                Topic.user_id == self.user_id,
                Topic.description.isnot(None),
                Topic.embedding.is_(None)
            ).first() is not None

            if has_missing_topics:
                logger.info(f"[CollapsedTreeCache] Detected missing topic embeddings for user {self.user_id}. Running embedding job...")
                db_session.close()
                run_embedding_job(user_id=self.user_id)
                # Clear tree cache and reload tree
                get_tree_cache.cache_clear()
                tree = get_tree_cache(user_id=self.user_id)
                db_session = Session()

            type_map_check = [
                ("knowledge", KnowledgeMemory),
                ("episodic",  EpisodicMemory),
                ("user",      UserMemory),
                ("decision",  DecisionMemory),
            ]
            has_missing_memories = False
            for mem_type, MemClass in type_map_check:
                query = db_session.query(MemClass).filter(
                    MemClass.user_id == self.user_id,
                    MemClass.content.isnot(None),
                    MemClass.embedding.is_(None)
                )
                if mem_type == "decision":
                    query = query.filter(MemClass.status.in_(["active", "conditional"]))
                if query.first() is not None:
                    has_missing_memories = True
                    break

            if has_missing_memories:
                logger.info(f"[CollapsedTreeCache] Detected missing memory embeddings for user {self.user_id}. Running memory embedding job...")
                db_session.close()
                run_memory_embedding_job(user_id=self.user_id)
                db_session = Session()

        except Exception as e:
            logger.info(f"[CollapsedTreeCache] Embeddings check/creation failed during build_all: {e}")

        all_nodes = [n for n in tree.topic_by_id.values() if n is not None]
        logger.info(f"[CollapsedTreeCache] Building pool from {len(all_nodes)} nodes...")

        entries: list = []
        embeddings: list = []

        def _get_path(node) -> str:
            parts = []
            current = node
            while current is not None:
                parts.append(current.name)
                current = tree.parent_map.get(current.id)
            return " > ".join(reversed(parts))

        # memory_rows[topic_id] = list of dicts
        memory_rows_by_topic = defaultdict(list)
        type_map = [
            ("knowledge", KnowledgeMemory),
            ("episodic",  EpisodicMemory),
            ("user",      UserMemory),
            ("decision",  DecisionMemory),
        ]
        for mem_type, MemClass in type_map:
            query = db_session.query(MemClass).filter(
                MemClass.user_id == self.user_id,
                MemClass.topic_id.isnot(None)
            )
            if mem_type == "decision":
                query = query.filter(MemClass.status.in_(["active", "conditional"]))
            for mem in query.all():
                if not mem.content:
                    continue
                ts_str = mem.timestamp.strftime("%Y-%m-%d %H:%M") if mem.timestamp else None
                vec = to_numpy(mem.embedding).copy() if mem.embedding else None
                if vec is None:
                    vec = embed_and_save(db_session, mem, mem.content)

                memory_rows_by_topic[mem.topic_id].append({
                    "id":         mem.id,
                    "type":       mem_type,
                    "content":    mem.content,
                    "timestamp":  ts_str,
                    "message_id": mem.message_id,
                    "vec":        vec,
                })

        # Build a name lookup for topics
        name_by_id  = {n.id: (n.name or "") for n in all_nodes}
        level_by_id = {n.id: (n.level or 0) for n in all_nodes}

        # Add branch/leaf topic nodes
        for node in all_nodes:
            is_leaf  = not bool(tree.children_map.get(node.id))
            path_str = _get_path(node)
            name     = node.name or ""

            # Inject topic-level summaries if requested by the user configuration
            if include_summaries:
                desc = (node.description or "").strip()
                if desc:
                    searchable = f"{path_str} {desc}".strip()
                    meta = NodeMeta(
                        topic_id=node.id,
                        name=name,
                        level=node.level or 0,
                        path=path_str,
                        is_leaf=is_leaf,
                        desc_words=len(desc.split()),
                        searchable_text=searchable,
                    )
                    entries.append(meta)
                    
                    node_emb = tree.embedding_cache.get(node.id)
                    if node_emb is None:
                        topic_row = db_session.query(Topic).filter(
                            Topic.id == node.id,
                            Topic.user_id == self.user_id
                        ).first()
                        if topic_row:
                            node_emb = embed_and_save(db_session, topic_row, desc)
                            tree.embedding_cache[node.id] = node_emb
                    embeddings.append(node_emb)

            mem_rows = memory_rows_by_topic.get(node.id, [])
            if not mem_rows:
                continue

            # Add individual memory rows for this leaf
            for row in mem_rows:
                topic_name = name_by_id.get(node.id, "")
                # BM25 text: topic path + content (topic is already at the end of path_str)
                searchable = f"{path_str} {row['content']}".strip()
                meta = MemoryMeta(
                    memory_id=row["id"],
                    memory_type=row["type"],
                    topic_id=node.id,
                    name=topic_name,
                    level=level_by_id.get(node.id, 0),
                    path=path_str,
                    is_leaf=True,
                    timestamp=row["timestamp"],
                    message_id=row["message_id"],
                    searchable_text=searchable,
                )
                entries.append(meta)
                embeddings.append(row["vec"])

        # BM25 pre-computation
        corpus_size = len(entries)
        
        # Build inverted index & calculate document lengths
        inverted_index = {}  # family -> {doc_idx: tf}
        doc_lengths = []
        union_find = UnionFind()
        
        for doc_idx, entry in enumerate(entries):
            # Tokenize entry searchable text (list of raw, lemma pairs)
            tokens = self._tokenize(entry.searchable_text)
            doc_lengths.append(len(tokens))
            
            # Count family frequencies in this document
            for raw, lemma in tokens:
                merge = union_find.union(raw, lemma)
                if merge:
                    obsolete, surviving = merge
                    if obsolete in inverted_index:
                        if surviving not in inverted_index:
                            inverted_index[surviving] = {}
                        for d_idx, tf in inverted_index[obsolete].items():
                            inverted_index[surviving][d_idx] = inverted_index[surviving].get(d_idx, 0) + tf
                        del inverted_index[obsolete]
                
                family = union_find.find(raw)
                if family not in inverted_index:
                    inverted_index[family] = {}
                inverted_index[family][doc_idx] = inverted_index[family].get(doc_idx, 0) + 1
                
        # Precompute IDF and avg doc length
        idf = self._compute_bm25_idf(inverted_index, corpus_size)
        avg_dl = sum(doc_lengths) / max(corpus_size, 1)

        # Embedding matrix
        embedding_matrix = None
        dim = None
        for e in embeddings:
            if e is not None:
                dim = len(e)
                break
        if dim is not None:
            rows_mat = [e if e is not None else np.zeros(dim, dtype=np.float32) for e in embeddings]
            raw_matrix = np.vstack(rows_mat).astype(np.float32)
            # Pre-normalize all rows so vector_score_all is a plain dot product.
            norms = np.linalg.norm(raw_matrix, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            embedding_matrix = (raw_matrix / norms).astype(np.float32)

        # Entry key index
        entry_key_to_index = {}
        for i, entry in enumerate(entries):
            if getattr(entry, "entry_type", "") == "memory":
                key = f"mem:{entry.memory_type}:{entry.memory_id}"
            else:
                key = f"node:{entry.topic_id}"
            entry_key_to_index[key] = i

        self._data = CollapsedTreeCacheData(
            entries=entries,
            inverted_index=inverted_index,
            doc_lengths=doc_lengths,
            idf=idf,
            avg_dl=avg_dl,
            corpus_size=corpus_size,
            union_find=union_find,
            embedding_matrix=embedding_matrix,
            entry_key_to_index=entry_key_to_index,
        )

        n_mems     = sum(1 for e in entries if getattr(e, "entry_type", "") == "memory")
        n_leaf     = sum(1 for e in entries if getattr(e, "entry_type", "") == "node" and e.is_leaf)
        n_branches = sum(1 for e in entries if getattr(e, "entry_type", "") == "node" and not e.is_leaf)
        logger.info(
            f"[CollapsedTreeCache] Pool ready: {corpus_size} entries "
            f"({n_mems} individual memories, {n_leaf} leaf nodes, {n_branches} branches)"
        )
        logger.info(f"[CollapsedTreeCache] BM25: {len(idf)} unique terms, avg_dl={avg_dl:.1f}")
        db_session.close()
        self.save()

    def ingest_memory(self, entry: CacheEntry, embedding: np.ndarray = None) -> None:
        """
        Incrementally ingest a new memory into the cache:
        1. Appends the entry to self._data.entries.
        2. Tokenizes the entry's searchable text and updates UnionFind.
        3. Merges inverted index posting lists when families merge.
        4. Updates document statistics and IDF.
        5. Updates the in-memory embedding matrix in-place.
        6. Persists the updated cache to disk.
        """
        if not self._data:
            raise ValueError("[CollapsedTreeCache] Cannot ingest memory: cache is not initialized.")

        # 1. Append entry and register key
        new_doc_idx = len(self._data.entries)
        self._data.entries.append(entry)
        
        if getattr(entry, "entry_type", "") == "memory":
            key = f"mem:{entry.memory_type}:{entry.memory_id}"
        else:
            key = f"node:{entry.topic_id}"
        self._data.entry_key_to_index[key] = new_doc_idx

        # 2. Tokenize and update Union-Find / postings
        tokens = self._tokenize(entry.searchable_text)
        self._data.doc_lengths.append(len(tokens))

        for raw, lemma in tokens:
            merge = self._data.union_find.union(raw, lemma)
            if merge:
                obsolete, surviving = merge
                if obsolete in self._data.inverted_index:
                    if surviving not in self._data.inverted_index:
                        self._data.inverted_index[surviving] = {}
                    for d_idx, tf in self._data.inverted_index[obsolete].items():
                        self._data.inverted_index[surviving][d_idx] = self._data.inverted_index[surviving].get(d_idx, 0) + tf
                    del self._data.inverted_index[obsolete]

            family = self._data.union_find.find(raw)
            if family not in self._data.inverted_index:
                self._data.inverted_index[family] = {}
            self._data.inverted_index[family][new_doc_idx] = self._data.inverted_index[family].get(new_doc_idx, 0) + 1

        # 3. Incremental IDF update — only recompute IDF for terms that appear
        self._data.corpus_size = len(self._data.entries)
        self._data.avg_dl = (
            self._data.avg_dl * (self._data.corpus_size - 1) + len(tokens)
        ) / self._data.corpus_size
        import math
        affected_families = set()
        for raw, _ in tokens:
            affected_families.add(self._data.union_find.find(raw))
        for family in affected_families:
            if family in self._data.inverted_index:
                df = len(self._data.inverted_index[family])
                self._data.idf[family] = math.log(
                    1 + (self._data.corpus_size - df + 0.5) / (df + 0.5)
                )

        # 4. Update embedding matrix
        if self._data.embedding_matrix is not None:
            dim = self._data.embedding_matrix.shape[1]
            if embedding is None:
                logger.info(f"[CollapsedTreeCache] Ingesting memory/node without embedding. Generating immediately...")
                from mindcache.Database.db_setup import Session, Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory
                db_sess = Session()
                vec = None
                try:
                    if getattr(entry, "entry_type", "") == "memory":
                        type_map = {
                            "knowledge": KnowledgeMemory,
                            "episodic":  EpisodicMemory,
                            "user":      UserMemory,
                            "decision":  DecisionMemory,
                        }
                        MemClass = type_map.get(entry.memory_type)
                        if MemClass:
                            mem_row = db_sess.query(MemClass).filter(
                                MemClass.id == entry.memory_id,
                                MemClass.user_id == self.user_id
                            ).first()
                            if mem_row:
                                vec = embed_and_save(db_sess, mem_row, mem_row.content)
                    else:
                        topic_row = db_sess.query(Topic).filter(
                            Topic.id == entry.topic_id,
                            Topic.user_id == self.user_id
                        ).first()
                        if topic_row:
                            vec = embed_and_save(db_sess, topic_row, topic_row.description)
                finally:
                    db_sess.close()
                if vec is None:
                    vec = np.zeros(dim, dtype=np.float32)
            else:
                vec = np.asarray(embedding, dtype=np.float32)
                v_norm = np.linalg.norm(vec)
                if v_norm > 0:
                    vec = vec / v_norm
            self._data.embedding_matrix = np.vstack(
                [self._data.embedding_matrix, vec.astype(np.float32)]
            )

        # 5. Persist to disk
        self.save()

    def save(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            orig_matrix = self._data.embedding_matrix
            orig_union_find = self._data.union_find

            # ── 1. Save the embedding matrix to a separate numpy binary file ──
            if orig_matrix is not None:
                np.save(self.emb_matrix_file, orig_matrix)

            # ── 2. Serialize UnionFind as two plain dicts (parent + size) ──────
            uf_parent, uf_size = orig_union_find.to_dicts() if orig_union_find else ({}, {})
            self._data.union_find = {"parent": uf_parent, "size": uf_size}  # plain dict for pickle

            # ── 3. Clear embedding_matrix before pickle (it lives in the .npy) ─
            self._data.embedding_matrix = None

            with open(self.cache_file, "wb") as f:
                pickle.dump(self._data, f, protocol=pickle.HIGHEST_PROTOCOL)

            # Restore live objects in memory
            self._data.embedding_matrix = orig_matrix
            self._data.union_find = orig_union_find

            # Save to in-memory registry
            _set_cached_collapsed_data(self.user_id, self._data)

            size_kb = self.cache_file.stat().st_size / 1024
            logger.info(f"[CollapsedTreeCache] Saved BM25 index to disk ({size_kb:.0f} KB): {self.cache_file}")
            if orig_matrix is not None:
                matrix_size_mb = self.emb_matrix_file.stat().st_size / (1024 * 1024)
                logger.info(f"[CollapsedTreeCache] Saved embedding matrix to disk ({matrix_size_mb:.2f} MB): {self.emb_matrix_file}")
        except Exception as e:
            logger.info(f"[CollapsedTreeCache] Save failed: {e}")

    def _reconstruct_embeddings_from_db(self) -> None:
        """Fetch all vector embeddings from DB and rebuild self._data.embedding_matrix in memory."""
        if not self._data or not self._data.entries:
            return

        from mindcache.Database.db_setup import Topic, EpisodicMemory, KnowledgeMemory, UserMemory, DecisionMemory, to_numpy, Session
        db_session = Session()

        try:
            from mindcache.Database.embedder import run_embedding_job, run_memory_embedding_job

            # Check missing topics for this user only
            has_missing_topics = db_session.query(Topic).filter(
                Topic.user_id == self.user_id,
                Topic.description.isnot(None),
                Topic.embedding.is_(None)
            ).first() is not None

            if has_missing_topics:
                logger.info(f"[CollapsedTreeCache] Detected missing topic embeddings for user {self.user_id}. Running embedding job...")
                db_session.close()
                run_embedding_job(user_id=self.user_id)
                db_session = Session()

            type_map_check = [
                ("knowledge", KnowledgeMemory),
                ("episodic",  EpisodicMemory),
                ("user",      UserMemory),
                ("decision",  DecisionMemory),
            ]
            has_missing_memories = False
            for mem_type, MemClass in type_map_check:
                query = db_session.query(MemClass).filter(
                    MemClass.user_id == self.user_id,
                    MemClass.content.isnot(None),
                    MemClass.embedding.is_(None)
                )
                if mem_type == "decision":
                    query = query.filter(MemClass.status.in_(["active", "conditional"]))
                if query.first() is not None:
                    has_missing_memories = True
                    break

            if has_missing_memories:
                logger.info(f"[CollapsedTreeCache] Detected missing memory embeddings for user {self.user_id}. Running memory embedding job...")
                db_session.close()
                run_memory_embedding_job(user_id=self.user_id)
                db_session = Session()

            # 1. Fetch all Topic embeddings for this user
            topics = db_session.query(Topic.id, Topic.embedding).filter(
                Topic.user_id == self.user_id,
                Topic.embedding.isnot(None)
            ).all()
            topic_embs = {tid: to_numpy(emb).copy() for tid, emb in topics}

            # 2. Fetch all memory embeddings for this user
            type_map = [
                ("knowledge", KnowledgeMemory),
                ("episodic",  EpisodicMemory),
                ("user",      UserMemory),
                ("decision",  DecisionMemory),
            ]
            mem_embs = {}
            for mem_type, MemClass in type_map:
                query = db_session.query(MemClass.id, MemClass.embedding).filter(
                    MemClass.user_id == self.user_id,
                    MemClass.topic_id.isnot(None),
                    MemClass.embedding.isnot(None)
                )
                if mem_type == "decision":
                    query = query.filter(MemClass.status.in_(["active", "conditional"]))
                for mid, emb in query.all():
                    mem_embs[f"{mem_type}:{mid}"] = to_numpy(emb).copy()

            # 3. Align with cached entries
            embeddings = []
            for entry in self._data.entries:
                if getattr(entry, "entry_type", "") == "memory":
                    key = f"{entry.memory_type}:{entry.memory_id}"
                    vec = mem_embs.get(key)
                else:
                    vec = topic_embs.get(entry.topic_id)
                embeddings.append(vec)

            # 4. Stack into matrix and normalize
            dim = None
            for e in embeddings:
                if e is not None:
                    dim = len(e)
                    break
            if dim is not None:
                rows_mat = [e if e is not None else np.zeros(dim, dtype=np.float32) for e in embeddings]
                raw_matrix = np.vstack(rows_mat).astype(np.float32)
                # Pre-normalize
                norms = np.linalg.norm(raw_matrix, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                self._data.embedding_matrix = (raw_matrix / norms).astype(np.float32)
                # Save reconstructed matrix to npy file
                np.save(self.emb_matrix_file, self._data.embedding_matrix)
                
        finally:
            db_session.close()

    def load(self) -> bool:
        if not self.cache_file.exists():
            logger.info("[CollapsedTreeCache] No cache file found. Will build on first query.")
            return False
        try:
            with open(self.cache_file, "rb") as f:
                data = pickle.load(f)
            if not isinstance(data, CollapsedTreeCacheData):
                raise ValueError("Cache file is stale or corrupt.")
            if not hasattr(data, "entries"):
                raise ValueError("Cache file is stale (missing entries).")
            if not hasattr(data, "inverted_index") or not hasattr(data, "doc_lengths"):
                raise ValueError("Cache file is stale (missing inverted_index or doc_lengths).")
            self._data = data

            uf_raw = getattr(self._data, "union_find", None)
            if isinstance(uf_raw, dict) and "parent" in uf_raw and "size" in uf_raw:
                self._data.union_find = UnionFind.from_dicts(uf_raw["parent"], uf_raw["size"])
                logger.info(f"[CollapsedTreeCache] Reconstructed UnionFind: {len(uf_raw['parent'])} terms in {len(set(uf_raw['parent'].values()))} families")
            elif isinstance(uf_raw, UnionFind):
                pass  # old pickle format — still valid
            else:
                self._data.union_find = UnionFind()  # empty — fresh start

            # Load the embedding matrix using zero-copy memory mapping
            n_entries = len(data.entries)
            if self.emb_matrix_file.exists():
                try:
                    loaded_matrix = np.load(self.emb_matrix_file, mmap_mode="r")
                    if loaded_matrix.shape[0] == n_entries:
                        self._data.embedding_matrix = loaded_matrix
                    else:
                        logger.info(f"[CollapsedTreeCache] Cache size mismatch: expected {n_entries} but npy has {loaded_matrix.shape[0]}. Reconstructing...")
                        self._reconstruct_embeddings_from_db()
                except Exception as npy_err:
                    logger.info(f"[CollapsedTreeCache] Failed to load npy file ({npy_err}). Reconstructing...")
                    self._reconstruct_embeddings_from_db()
            else:
                logger.info("[CollapsedTreeCache] Embeddings matrix file missing. Reconstructing...")
                self._reconstruct_embeddings_from_db()

            n = len(data.entries)
            size_kb = self.cache_file.stat().st_size / 1024
            logger.info(f"[CollapsedTreeCache] Loaded BM25 index from disk: {n} entries ({size_kb:.0f} KB)")
            if self._data.embedding_matrix is not None:
                logger.info(f"[CollapsedTreeCache] Loaded memory-mapped embedding matrix: shape {self._data.embedding_matrix.shape}")
            
            # Save to in-memory registry
            _set_cached_collapsed_data(self.user_id, self._data)
            return True
        except Exception as e:
            logger.info(f"[CollapsedTreeCache] Load failed ({e}). Will rebuild.")
            self._data = None
            return False

    def clear(self) -> None:
        self._data = None
        _clear_cached_collapsed_data(self.user_id)
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
                logger.info(f"[CollapsedTreeCache] Cache file deleted.")
            except Exception as e:
                logger.info(f"[CollapsedTreeCache] Could not delete cache file: {e}")
        if self.emb_matrix_file.exists():
            try:
                self.emb_matrix_file.unlink()
                logger.info(f"[CollapsedTreeCache] Embeddings matrix file deleted.")
            except Exception as e:
                logger.info(f"[CollapsedTreeCache] Could not delete embeddings matrix file: {e}")


collapsed_tree_cache = CollapsedTreeCache()


if __name__ == "__main__":
    cache = CollapsedTreeCache()
    cache.clear()