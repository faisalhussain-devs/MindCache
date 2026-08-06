# 📜 Incremental Delta Summaries

Broad high-level questions (e.g. *"What projects have I worked on this month?"* or *"Summarize my backend architecture decisions"*) fail when relying solely on flat vector search, because individual memory snippets lack the broad context required for synthesis.

MindCache addresses this with **Incremental Delta Summaries**—a bottom-up RAPTOR-style summary tree maintained across topic hierarchy nodes.

---

## 🔄 Summary Lifecycle & Delta Rollups

```
Leaf Memories Added (Session 1..N)
             ↓
    Delta Processing (Only new items)
             ↓
Parent Node Summary Updated (Incremental Rollup)
             ↓
Available for High-Level Summarization Queries
```

### Key Principles

1. **Bottom-Up Rollup**: Summaries are built hierarchically starting from leaf memory nodes up to parent topic nodes.
2. **Delta Processing**: When new conversation turns arrive, MindCache does **not** re-summarize entire subtrees. Instead, it extracts the new memory "deltas" (new additions) and updates existing parent summaries incrementally. This drastically reduces LLM token consumption.
3. **Adaptive Activation**: Summary nodes are indexed alongside standard memories and are pulled during online retrieval whenever the **Adaptive Query Classifier** detects a broad overview query.

---

## 💡 Evaluation Impact

Across our BEAM QA follow-up evaluation, hierarchical summaries:
- Activated on **4–6 broad queries per conversation**.
- Consistently improved response quality for multi-session synthesis and high-level summaries.
- Directly converted multiple benchmark failures into strict passes.

---

## 🔗 Related Documentation

- **[Automatically Organized Topic Hierarchy](topic-tree.md)**: Tree node structures and Leiden partitioning.
- **[MindCache Architecture](architecture.md)**: Background worker dynamic tree lifecycle.
