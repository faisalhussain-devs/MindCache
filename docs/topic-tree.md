# 🌲 Automatically Organized Topic Hierarchy

Rather than storing memories as a flat pool of embedding vectors, **MindCache continuously organizes memories into a living topic hierarchy**. Leaf nodes store individual extracted memories, while internal nodes organize concepts and power hierarchical retrieval and summarization.

<p align="center">
  <img src="https://github.com/user-attachments/assets/cc34f593-46c4-4369-b8ba-e2a16fca3f77" width="700" alt="Automatically Organized Topic Hierarchy" />
</p>

---

## 🎯 How Topic Hierarchy Works

### 1. Grounded Path Routing
When new conversation turns are processed, MindCache queries existing top-level paths in the database and passes them to the extractor prompt as constraints.
- **Goal**: Prevents creating duplicate topic branches (e.g. creating both `Python Development` and `Python Programming`).
- **Mechanism**: The extraction pipeline routes newly extracted memories to the most suitable existing path or creates a new child topic branch under an existing parent node if needed.

### 2. Leaf Nodes vs. Internal Nodes
- **Leaf Nodes**: Contain memory clusters (e.g. `[memories: 47]`) storing raw User, Decision, Episodic, and Knowledge facts.
- **Internal Nodes**: Represent higher-level semantic concepts (e.g. `Software Architecture → Database Engineering`). Internal nodes maintain **Incremental Delta Summaries** that synthesize the child nodes underneath them.

---

## ✂️ Dynamic Graph Restructuring

As memories accumulate, static hierarchies become rigid or unbalanced. MindCache applies background dynamic graph operations to maintain topic hygiene:

### Node Splitting
When a leaf node's memory count exceeds a designated threshold, MindCache automatically splits the overloaded node into smaller, focused child nodes using embedding clustering.

### Sibling Node Merging
If multiple sibling nodes under the same parent become sparse or semantic drift causes overlap, MindCache merges them back into a consolidated topic node.

### Leiden Graph Partitioning
Periodically, MindCache builds a semantic similarity graph of stored memories and executes **Leiden graph partitioning** to detect natural topic clusters and adjust parent-child relationships accordingly.

---

## ⏭️ Read Next

- **[Incremental Delta Summaries](summaries.md)** — Bottom-up node rollups across topic tree nodes.
- **[Hierarchical Path Indexing](path-indexing.md)** — Prepending path terms to sparse BM25 indices.
- **[MindCache Architecture](architecture.md)** — Background worker dynamic tree lifecycle.
