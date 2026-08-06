# 🔎 Online Multi-Stage Hybrid Retrieval Engine

MindCache provides a low-latency (**1.08s average**), high-accuracy retrieval engine built to prevent flat vector retrieval failures like **context inflation**, **temporal collapse**, and **broad-query failure**.

```mermaid
flowchart LR
    classDef dark      fill:#313244,color:#cdd6f4,stroke:#585b70
    classDef llm       fill:#cba6f7,color:#11111b,stroke:#11111b
    classDef search    fill:#89b4fa,color:#11111b,stroke:#11111b
    classDef anchor    fill:#f38ba8,color:#11111b,stroke:#11111b
    classDef green     fill:#a6e3a1,color:#11111b,stroke:#11111b

    subgraph PhaseA ["1. Hybrid RRF Search"]
        Q["🔎 User Query"]:::dark --> S["Vector + BM25"]:::search
        S --> RRF["Reciprocal Rank Fusion"]:::search
    end

    subgraph PhaseB ["2. Memory Pools & Anchors"]
        RRF --> CL["🏷️ Query Classifier"]:::llm
        CL --> P["Memory Pools & Summaries"]:::search
        P --> DA["⚖️ Decision Anchors"]:::anchor
        DA --> EX["BM25 Anchor Expansion"]:::anchor
    end

    subgraph PhaseC ["3. Prompt Assembly"]
        EX --> INJ["📋 Inject Directives"]:::anchor
        INJ --> OUT["📤 Final Context → LLM"]:::green
    end
```

---

## 🚀 Key Pipeline Components

### 1. Hybrid Vector + BM25 + Reciprocal Rank Fusion (RRF)
Vector embeddings excel at semantic similarity, but often miss exact technical terms (e.g. error codes, library versions, database names). BM25 handles sparse lexical precision.
- **RRF Scoring**: Combines ranks from dense vector search and sparse BM25 search using:
  $$RRF(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
  This eliminates score normalization issues between vector distances and BM25 scores.

### 2. BM25 Morphological Aliasing
Standard BM25 treats different word inflections as entirely separate tokens. MindCache implements an in-memory **Union-Find data structure with lemmatization** to unify word inflections (`database` / `databases` / `databasemanagement`) into canonical equivalence classes during sparse BM25 indexing and querying.

### 3. Adaptive Query Classification
When a query arrives, MindCache classifies the user's intent into:
- **Specific Factual Queries**: Directly targets specific leaf nodes and memory facts.
- **Broad Overview Queries**: Automatically activates high-level [Incremental Delta Summaries](summaries.md) from upper topic tree nodes.

### 4. Decision-Anchor BM25 Expansion
Active decisions act as retrieval anchors. When a relevant active decision is retrieved, MindCache extracts key decision terms and performs secondary BM25 expansion queries to pull in supporting Episodic and Knowledge memories that standard vector search might miss.

---

## ⏭️ Read Next

- **[How Retrieval Works](how-retrieval-works.md)** — Trace the step-by-step query execution pipeline.
- **[Decision-Anchor Retrieval](decision-anchor-retrieval.md)** — Decision state machine & expansion details.
- **[Retrieval Budgeting](retrieval-budgeting.md)** — Token quota allocations per memory category.
