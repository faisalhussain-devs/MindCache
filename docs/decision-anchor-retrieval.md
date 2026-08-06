# ⚖️ Decision State Tracking & Decision-Anchor Retrieval

One of the most persistent failure modes in LLM agent memory is **temporal collapse**—where older, abandoned choices (e.g. *"We use MySQL for our backend"*) remain in vector stores and compete equally with newer choices (e.g. *"We switched to PostgreSQL"*).

MindCache resolves this using **Decision State Tracking** combined with **Decision-Anchor Expansion**.

---

## 🔄 Decision State Machine

Every decision memory extracted by MindCache is assigned an explicit state by the **Decision Analyzer**:

```
Decision: Use PyTorch for deep learning
Status:   ACTIVE

History:
  TensorFlow (2024-01-10) -> REJECTED
       ↓
  PyTorch Experiment (2024-02-15) -> CONDITIONAL
       ↓
  PyTorch Selection (2024-03-01) -> ACTIVE
```

### Decision States:
- **`ACTIVE`**: The current authoritative choice. Included in prompt context.
- **`SUPERSEDED`**: Replaced by a newer decision. Excluded from active prompt injection to prevent conflicts.
- **`CONDITIONAL`**: Active only under specific constraints or environments.
- **`REJECTED`**: Explicitly evaluated and turned down.

---

## ⚓ Decision-Anchor Expansion

Top-ranked active decisions often serve as the central context for user queries. MindCache uses retrieved active decisions as **semantic anchors** to expand search:

1. **Anchor Identification**: Top-scoring `ACTIVE` decisions are selected from initial hybrid retrieval.
2. **Concept Extraction**: Key entities, technologies, or keywords are extracted from the active decision string.
3. **Lexical BM25 Expansion**: MindCache executes secondary BM25 expansion queries using these anchor terms.
4. **Context Enrichment**: Supporting Episodic and Knowledge memories that may lack vector embedding similarity to the initial user prompt are retrieved and attached.

---

## 🔗 Related Documentation

- **[Specialized Memory Types](memory-types.md)**: Four specialized memory categories.
- **[Online Hybrid Retrieval Engine](retrieval.md)**: Overall retrieval engine overview.
