# 🧠 Core Architectural Ideas

During the development and evaluation of MindCache, five core architectural choices were established and retained in the system.

> **These design choices address common long-term memory failures such as context inflation, temporal collapse, and poor broad-query retrieval.**

---

## 🏛️ The Five Architectural Pillars

### 1. 🧩 Specialized Memory Types
Separating memories into four semantic categories—**User**, **Decision**, **Episodic**, and **Knowledge**—enables specialized update lifecycles and structured prompt formatting, preventing single high-density categories from swamping context space.
👉 **[Read more](memory-types.md)**

### 2. 📊 Retrieval Budgeting Per Memory Type
Rather than filling the context window with the globally highest-scoring memories (which often results in a single memory category dominating), MindCache allocates explicit retrieval quotas across memory types. This guarantees evidence diversity in every prompt.
👉 **[Read more](retrieval-budgeting.md)**

### 3. 🌲 Dynamic Hierarchical Tree & Incremental Summaries
Incremental RAPTOR-style hierarchical summaries improved retrieval for broad multi-topic queries where semantic vector search alone often struggled.
👉 **[Read more](topic-tree.md)** & **[Incremental Summaries](summaries.md)**

### 4. ⚖️ Decision-Guided Retrieval (Decision Anchors)
Top-ranked Decision memories act as semantic anchors. MindCache extracts key concepts from active decisions and performs BM25 lexical expansion to pull in supporting Episodic and Knowledge memories that standard vector search can miss.
👉 **[Read more](decision-anchor-retrieval.md)**

### 5. 🗂️ Hierarchical Path Indexing
Stored memories index their complete tree path (e.g., `Artificial Intelligence → Machine Learning → Deep Learning → PyTorch`). This provides additional lexical context that improves BM25 recall for broader conceptual queries.
👉 **[Read more](path-indexing.md)**

---

## 📊 Architectural Findings Evidence

| Finding | Evaluation Evidence | Evidence Level |
| :--- | :--- | :--- |
| **Hierarchical summaries** | Typically 4–6 activations per conversation; consistently improved retrieval quality | 📊 **Quantitatively Supported** |
| **Decision anchors** | Manual retrieval analysis across development | 🔍 **Observed in Development** |
| **Hierarchical path indexing** | Manual inspection of retrieved evidence across representative queries | 🔍 **Observed in Development** |
| **Four memory types** | Architectural schema refinement | 🏗️ **Design Rationale** |
| **Memory type quotas** | Iterative architectural refinement | 🏗️ **Design Rationale** |

---

## ⏭️ Read Next

- **[How Retrieval Works](how-retrieval-works.md)** — Step-by-step trace of `mc.search()` query execution.
- **[MindCache Architecture](architecture.md)** — High-level system overview across all three phases.
