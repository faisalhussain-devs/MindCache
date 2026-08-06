# 🔄 How Retrieval Works (End-to-End Walkthrough)

This document provides a step-by-step walkthrough of exactly what happens when `mc.search(query, user_id)` is invoked in MindCache.

Rather than scattering the execution flow across separate features, this guide traces a single query execution from initial user input to final assembled prompt context.

---

## 🗺️ Complete Retrieval Execution Flow

```
User Query: mc.search("What is Alice's preferred database and deep learning framework?")
   │
   ├──> 1. Parallel Dual Search
   │      ├── Dense Embedding Search (Cosine similarity)
   │      └── Sparse Lexical Search (BM25 with Morphological Aliasing & Path Indexing)
   │
   ├──> 2. Reciprocal Rank Fusion (RRF)
   │      └── Merges dense & sparse candidate ranks into a unified candidate pool
   │
   ├──> 3. Adaptive Query Classification
   │      ├── Specific Factual Query ──> Focus on leaf memory nodes
   │      └── Broad Overview Query   ──> Trigger High-Level Summary Injection
   │
   ├──> 4. Decision Anchor Expansion
   │      ├── Identify top ACTIVE Decision memories
   │      └── Run secondary BM25 expansion queries for decision concepts
   │
   ├──> 5. Memory Pool Partitioning & Budget Allocation
   │      ├── User Memory Quota       (15% - 20%)
   │      ├── Active Decision Quota   (20% - 25%)
   │      ├── Knowledge & Summaries   (25% - 30%)
   │      └── Episodic Event Quota    (30% - 35%)
   │
   └──> 6. Final Prompt Context Assembly
          └── Formatted structured context injected into LLM System Prompt
```

---

## 🔍 Step-by-Step Execution Breakdown

### Step 1: Parallel Dual Search
When `mc.search(query)` receives the query string:
1. **Dense Vector Search**: Embeds the query and computes cosine similarity across stored memory vector indices.
2. **Sparse Lexical Search**: Runs BM25 scoring over memory strings, leveraging **BM25 Morphological Aliasing** (Union-Find lemmatization) and **Hierarchical Path Indexing** (matching full topic tree path terms).

### Step 2: Reciprocal Rank Fusion (RRF)
Dense vector distance and sparse BM25 scores are on incompatible scales. MindCache combines their rank positions using RRF:
$$RRF(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
This produces a single, non-biased candidate list containing both semantic matches and keyword-exact matches.

### Step 3: Adaptive Query Classification
The query classifier checks if the query requires high-level synthesis:
- **Factual Queries** (e.g. *"What port does Postgres run on?"*): Proceeds directly with retrieved leaf memories.
- **Broad Overview Queries** (e.g. *"Summarize my backend tech stack history"*): Activates parent node **Incremental Delta Summaries** from upper topic tree levels and appends them to the candidate pool.

### Step 4: Decision Anchor Expansion
MindCache scans the top-ranked candidate pool for `ACTIVE` decision memories (e.g. *"Switched from TensorFlow to PyTorch"*).
- Extracted decision keywords serve as **search anchors**.
- MindCache executes secondary BM25 queries to fetch supporting Episodic and Knowledge memories connected to that decision, ensuring no supporting context is missed.

### Step 5: Memory Pool Partitioning & Retrieval Budgeting
To prevent **context inflation** (where 30 similar log entries fill the prompt), candidates are partitioned into four memory pools and constrained by explicit token quotas:
- **User Memory Pool**: Guarantees user persona & preferences remain intact.
- **Decision Memory Pool**: Enforces active architectural decisions.
- **Knowledge / Summary Pool**: Supplies domain rules and topic overviews.
- **Episodic Memory Pool**: Provides specific event evidence.

### Step 6: Final Prompt Context Assembly
The selected candidates are formatted into a clean, human-readable structured context block ready for injection into the LLM system prompt:

```text
[USER MEMORY]
• Prefers Python, FastAPI, and Postgres for backend development.

[DECISION MEMORY (ACTIVE)]
• Switched from TensorFlow to PyTorch for model training.

[KNOWLEDGE MEMORY]
• Completed CS50 AI course; familiar with transformer architectures.

[HIERARCHICAL SUMMARY]
• Machine Learning Journey: Transitioned from TF to PyTorch, built MindCache core.
```

---

## ⏭️ Read Next

- **[Online Hybrid Retrieval Engine](retrieval.md)** — Detailed specification of vector + BM25 + RRF ranking algorithms.
- **[Decision-Anchor Retrieval](decision-anchor-retrieval.md)** — Decision state machine and expansion logic.
- **[Retrieval Budgeting](retrieval-budgeting.md)** — Token quota allocations per memory category.
- **[MindCache Architecture](architecture.md)** — High-level system overview across all three phases.
