# 🧠 Core Architectural Ideas & Evidence Matrix

During the development and evaluation of MindCache, seven core architectural components were established and retained in the system.

> **These design choices address common long-term memory failures such as context inflation, temporal collapse, and poor broad-query retrieval.**

---

## 🔬 Component Engineering & Evidence Matrix

Seven components were evaluated; five are treated as the primary architectural pillars, with retrieval primitives such as BM25/RRF and path indexing supporting those pillars.

| MindCache Component | Problem Targeted | Architectural Design | Evidence Source | Evidence Strength |
| :--- | :--- | :--- | :--- | :--- |
| **Memory types** | Different information behaves differently | Partitioned into User, Decision, Episodic, Knowledge | Development + evaluation | Moderate |
| **Topic hierarchy** | Flat memory loses structural context | Living topic tree with dynamic splitting & Leiden partitioning | Development evidence | Moderate |
| **Incremental summaries** | Broad queries require synthesis | Bottom-up delta rollups across topic tree nodes | BEAM QA + ablation | **Strong** |
| **Decision lifecycle** | Old decisions conflict with current choices | Active vs Superseded state machine tracking | Contradiction evaluation | **Strong** |
| **BM25 + dense search** | Exact technical terms missed by vectors | Hybrid Reciprocal Rank Fusion (RRF) search | Retrieval experiments | Strong |
| **Decision anchors** | Current decisions buried during retrieval | Query-time BM25 anchor expansion | Development/ablation | Empirical |
| **Path indexing** | Lexical search results lack structural context | Prepending full topic path terms to BM25 index | Development inspection | Observed |

---

## 🏛️ The Five Architectural Pillars

### 1. 🧩 Specialized Memory Types
Separating memories into four semantic categories—**User**, **Decision**, **Episodic**, and **Knowledge**—enables specialized update lifecycles and structured prompt formatting; retrieval budgeting then prevents any single memory type from dominating the final context.
👉 **[Read more](memory-types.md)**

### 2. 📊 Retrieval Budgeting Per Memory Type
Rather than filling the context window with the globally highest-scoring memories (which often results in a single memory category dominating), MindCache allocates explicit retrieval quotas across memory types. This guarantees evidence diversity in every prompt.
👉 **[Read more](retrieval-budgeting.md)**

### 3. 🌲 Dynamic Hierarchical Tree & Incremental Summaries
Incremental summaries improved retrieval for broad multi-topic queries where semantic vector search alone often struggled.
👉 **[Read more](topic-tree.md)** & **[Incremental Summaries](summaries.md)**

### 4. ⚖️ Decision-Guided Retrieval (Decision Anchors)
Top-ranked Decision memories act as semantic anchors. MindCache extracts key concepts from active decisions and performs BM25 lexical expansion to pull in supporting Episodic and Knowledge memories that standard vector search can miss.
👉 **[Read more](decision-anchor-retrieval.md)**

### 5. 🗂️ Hierarchical Path Indexing
Stored memories index their complete tree path (e.g., `Artificial Intelligence → Machine Learning → Deep Learning → PyTorch`). This provides additional lexical context that improves BM25 recall for broader conceptual queries.
👉 **[Read more](path-indexing.md)**

---

## ⏭️ Read Next

- **[Deep Competitive Analysis](competitive-analysis.md)** — Architectural comparison across Mem0, Graphiti, Letta, MemOS, Neo4j, and Zep.
- **[BEAM QA Benchmark Evaluation](evaluation.md)** — Comprehensive evaluation metrics and category pass rates.
- **[How Retrieval Works](how-retrieval-works.md)** — Step-by-step trace of `mc.search()` query execution.
