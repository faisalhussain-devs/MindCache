# 🗂️ Hierarchical Path Indexing

Standard memory search indexes isolated memory text strings without structural awareness of where the memory lives in the knowledge tree.

MindCache implements **Hierarchical Path Indexing**, attaching full topic paths directly to leaf memory indices.

---

## 🌲 Full-Path Representation

When memories are indexed for sparse BM25 retrieval, MindCache prepends their complete structural path within the topic hierarchy:

```text
Full Index String:
"Artificial Intelligence → Machine Learning → Deep Learning → PyTorch :: Installed PyTorch 2.2 with CUDA 12.1 support."
```

### Advantages

1. **Enhanced Lexical Context**: A memory stating *"Installed 2.2 with CUDA 12.1 support"* might fail a BM25 query for *"PyTorch deep learning setup"*. Including the ancestor path terms (`Artificial Intelligence`, `Machine Learning`, `Deep Learning`, `PyTorch`) ensures sparse BM25 keyword matching succeeds.
2. **Disambiguation**: Distinguishes memories with identical phrasing that belong to different domain branches (e.g. `Databases → Indexing` vs. `Search Engines → Indexing`).
3. **Hierarchical Filtering**: Enables fast substring and prefix matching along active tree paths.

---

## 🔗 Related Documentation

- **[Automatically Organized Topic Hierarchy](topic-tree.md)**: Tree construction and node splitting.
- **[Online Hybrid Retrieval Engine](retrieval.md)**: Vector + BM25 hybrid search engine.
