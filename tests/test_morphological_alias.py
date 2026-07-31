import pytest
import numpy as np
from mindcache.retrieval.root_cache import CollapsedTreeCache, CollapsedTreeCacheData, UnionFind, MemoryMeta, NodeMeta

def test_union_find_basic():
    uf = UnionFind()
    # Dynamic insertion
    assert uf.find("tracking") == "tracking"
    assert uf.find("track") == "track"
    
    # Union
    merge = uf.union("tracking", "track")
    assert merge is not None
    obsolete, surviving = merge
    assert {obsolete, surviving} == {"tracking", "track"}
    
    # Find representative
    assert uf.find("tracking") == uf.find("track")
    
    # Incremental union
    merge2 = uf.union("tracked", "track")
    assert merge2 is not None
    assert uf.find("tracked") == uf.find("track")
    
    # Verify no redundant merge
    assert uf.union("tracked", "tracking") is None

def test_tokenizer_returns_pairs():
    cache = CollapsedTreeCache()
    # Test typical query string
    pairs = cache._tokenize("Have you used any sleep tracking devices to monitor my sleep?")
    assert len(pairs) > 0
    # Every token should be a (raw, lemma) pair
    for raw, lemma in pairs:
        assert isinstance(raw, str)
        assert isinstance(lemma, str)
        assert raw == raw.lower()
        assert lemma == lemma.lower()

def test_spacy_pos_mismatch_resolution():
    cache = CollapsedTreeCache()
    
    # spaCy tags "tracking" differently depending on context:
    # 1. NOUN context: "Uses a Xiaomi Mi Band 7 for sleep tracking." -> lemma: "tracking"
    # 2. VERB context: "Uses a Xiaomi Mi Band 6 for sleep tracking and logging." -> lemma: "track"
    pairs1 = cache._tokenize("Uses a Xiaomi Mi Band 7 for sleep tracking.")
    pairs2 = cache._tokenize("Uses a Xiaomi Mi Band 6 for sleep tracking and logging.")
    
    tracking_lemma1 = [lemma for raw, lemma in pairs1 if raw == "tracking"][0]
    tracking_lemma2 = [lemma for raw, lemma in pairs2 if raw == "tracking"][0]
    
    # Verify they have different lemmas initially
    assert tracking_lemma1 == "tracking"
    assert tracking_lemma2 == "track"
    
    # Build a DSU and simulate unioning all pairs
    uf = UnionFind()
    for raw, lemma in pairs1 + pairs2:
        uf.union(raw, lemma)
        
    # Verify both forms resolve to the same family representative!
    assert uf.find("tracking") == uf.find("track")

def test_inverted_index_family_merges():
    # Setup manual data for test
    uf = UnionFind()
    # Initial state: separate families "tracking" and "track"
    uf.union("tracking", "tracking")
    uf.union("track", "track")
    
    doc_lengths = [1, 1]
    entries = [
        MemoryMeta(memory_id=1, memory_type="user", topic_id=1, name="T1", level=0, path="P1", searchable_text="tracking"),
        MemoryMeta(memory_id=2, memory_type="user", topic_id=1, name="T1", level=0, path="P1", searchable_text="track")
    ]
    
    # Inverted index with separate keys
    inverted_index = {
        "tracking": {0: 1},
        "track": {1: 1}
    }
    
    # Merge "tracking" and "track"
    merge = uf.union("tracking", "track")
    assert merge is not None
    obsolete, surviving = merge
    
    # Trigger index merge
    if obsolete in inverted_index:
        if surviving not in inverted_index:
            inverted_index[surviving] = {}
        for d_idx, tf in inverted_index[obsolete].items():
            inverted_index[surviving][d_idx] = inverted_index[surviving].get(d_idx, 0) + tf
        del inverted_index[obsolete]
        
    # Verify postings are merged under the surviving root
    assert obsolete not in inverted_index
    assert surviving in inverted_index
    assert inverted_index[surviving] == {0: 1, 1: 1}

def test_query_fallback_fallback_distinct_families():
    cache = CollapsedTreeCache()
    # Setup cache data manually where "tracking" and "track" have not been merged
    uf = UnionFind()
    uf.union("tracking", "tracking")
    uf.union("track", "track")
    
    inverted_index = {
        "tracking": {0: 2},
        "track": {1: 1}
    }
    doc_lengths = [3, 3]
    corpus_size = 2
    avg_dl = 3.0
    
    cache._data = CollapsedTreeCacheData(
        entries=[None, None],
        inverted_index=inverted_index,
        doc_lengths=doc_lengths,
        idf={},
        avg_dl=avg_dl,
        corpus_size=corpus_size,
        union_find=uf,
        embedding_matrix=None,
        entry_key_to_index={}
    )
    cache._data.idf = cache._compute_bm25_idf(inverted_index, corpus_size)
    
    # Query with fallback: query token has raw="tracking", lemma="track"
    # Since they are different families, query fallback must merge them in memory
    query_tokens = [("tracking", "track")]
    scores = cache.bm25_score_all(query_tokens, k1=1.5, b=0.75)
    
    # Verify scores are computed for both document 0 and document 1
    assert scores[0] > 0.0
    assert scores[1] > 0.0

def test_incremental_memory_ingestion():
    cache = CollapsedTreeCache()
    uf = UnionFind()
    # Initialize cache data with 1 entry
    entry1 = MemoryMeta(memory_id=1, memory_type="user", topic_id=1, name="T1", level=0, path="P1", searchable_text="track")
    
    cache._data = CollapsedTreeCacheData(
        entries=[entry1],
        inverted_index={"track": {0: 1}},
        doc_lengths=[1],
        idf={"track": 0.5},
        avg_dl=1.0,
        corpus_size=1,
        union_find=uf,
        embedding_matrix=np.zeros((1, 5), dtype=np.float32),
        entry_key_to_index={"mem:user:1": 0}
    )
    
    # Mock save method so it doesn't try to write to disk during test
    saved = []
    cache.save = lambda: saved.append(True)
    
    # Ingest a memory with "tracking"
    entry2 = MemoryMeta(memory_id=2, memory_type="user", topic_id=1, name="T1", level=0, path="P1", searchable_text="tracking")
    cache.ingest_memory(entry2, embedding=np.ones(5, dtype=np.float32))
    
    # Verify entry added
    assert len(cache._data.entries) == 2
    assert cache._data.corpus_size == 2
    assert cache._data.entries[1] == entry2
    
    # Verify union occurred incrementally: "tracking" and "track" merged!
    assert cache._data.union_find.find("tracking") == cache._data.union_find.find("track")
    surviving_family = cache._data.union_find.find("track")
    obsolete_family = "tracking" if surviving_family == "track" else "track"
    
    # Verify posting lists merged correctly
    assert obsolete_family not in cache._data.inverted_index
    assert surviving_family in cache._data.inverted_index
    assert cache._data.inverted_index[surviving_family] == {0: 1, 1: 1}
    
    # Verify embedding matrix shape updated in-place
    assert cache._data.embedding_matrix.shape == (2, 5)
    assert np.allclose(cache._data.embedding_matrix[1], 1.0)
    
    # Verify save was called
    assert len(saved) == 1
