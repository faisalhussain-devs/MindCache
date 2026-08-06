# 📊 Retrieval Budgeting & Context Partitioning

In flat vector databases, top-K search simply selects the K memories with the highest similarity scores. Over extended usage, this leads to **context inflation** and **category dominance**—where dozens of similar episodic log entries fill the entire prompt context window, completely crowding out vital active user preferences or architectural decisions.

MindCache solves this by enforcing **Memory Type Budgeting**.

---

## 🎯 Context Allocation Strategy

When assembling context for an LLM prompt, MindCache partitions the total target token limit into explicit category quotas:

| Memory Category | Target Quota Allocation | Rationale |
| :--- | :---: | :--- |
| **User Memory** | 15% - 20% | Guarantees user persona, coding preferences, and constraints are always visible. |
| **Active Decisions** | 20% - 25% | Ensures current architectural and project choices anchor the prompt. |
| **Knowledge Memories**| 25% - 30% | Supplies technical documentation, codebase facts, or top-level topic overviews. |
| **Episodic Memories** | 30% - 35% | Provides specific historical turn evidence relevant to the query. |
| **Summaries** | OPTIONAL | Provides High level details and gives a summary about the entire topic. |
---

## 💡 Benefits

1. **Guaranteed Evidence Diversity**: Prevents any single memory type from swamping the prompt.
2. **Predictable Prompt Tokens**: Keeps context size balanced and stable across multi-turn conversations.
3. **Structured Ingestion Directives**: Forces LLM generation to adhere strictly to retrieved constraints.

---

## 🔗 Related Documentation

- **[Specialized Memory Types](memory-types.md)**: Deep dive into User, Decision, Episodic, and Knowledge types.
- **[Online Hybrid Retrieval Engine](retrieval.md)**: Full retrieval pipeline specification.
