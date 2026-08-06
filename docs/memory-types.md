# 🧩 Specialized Memory Types

Traditional memory systems treat all extracted statements as generic unstructured text strings or vectors. MindCache categorizes every extracted fact into **Four Specialized Memory Types**, each with its own update lifecycle and retrieval behavior.

---

## 📚 The Four Memory Types

```
┌────────────────────────────────────────────────────────┐
│ 👤 USER MEMORY                                         │
│ Explicit user preferences, roles, constraints, habits  │
├────────────────────────────────────────────────────────┤
│ ⚖️ DECISION MEMORY                                      │
│ Architectural/technical choices, status, rationale     │
├────────────────────────────────────────────────────────┤
│ 📖 EPISODIC MEMORY                                     │
│ Specific historical interactions, events, sessions     │
├────────────────────────────────────────────────────────┤
│ 💡 KNOWLEDGE MEMORY                                    │
│ Extracted domain facts, technical documentation, rules │
└────────────────────────────────────────────────────────┘
```

### 1. 👤 User Memory
- **Purpose**: Stores explicit user preferences, skills, role constraints, and personal style.
- **Example**: *"Prefers Python and FastAPI for backend development, and Postgres for database."*
- **Lifecycle**: Updated when new user preferences contradict or update past preferences.

### 2. ⚖️ Decision Memory
- **Purpose**: Tracks explicit technical, project, or personal choices alongside their current status (`ACTIVE`, `SUPERSEDED`, `CONDITIONAL`, `REJECTED`).
- **Example**: *"Switched from TensorFlow to PyTorch for model training (Status: ACTIVE)."*
- **Lifecycle**: Managed by the **Decision State Analyzer** to ensure outdated choices are marked as superseded and do not contaminate LLM prompts.

### 3. 📖 Episodic Memory
- **Purpose**: Captures time-stamped events, specific task executions, debugging sessions, and context bound to a particular point in time.
- **Example**: *"Configured Docker container for PostgreSQL instance on 2024-03-12."*
- **Lifecycle**: Append-mostly with optional time decay or chronological sequence linking.

### 4. 💡 Knowledge Memory
- **Purpose**: Stores general domain facts, codebase structures, external API rules, and project specifications.
- **Example**: *"MindCache uses SQLite by default with optional PostgreSQL pgvector backend support."*
- **Lifecycle**: Updated when underlying documentation or specifications change.

---

## 🎯 Why Memory Type Separation Matters

1. **Prevents Retrieval Bias**: In flat vector stores, single high-embedding-similarity categories (like raw event logs) can crowd out critical active decisions or user constraints.
2. **Specialized Update Rules**: A decision requires state tracking (`Active` vs `Superseded`), whereas a user preference requires constraint enforcement and an episodic event requires chronological tracking.
3. **Structured Prompt Formatting**: Allows MindCache to inject neatly partitioned memory blocks into the LLM system prompt:

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

- **[Retrieval Budgeting](retrieval-budgeting.md)** — Quota allocation across memory types.
- **[Decision-Anchor Retrieval](decision-anchor-retrieval.md)** — Decision state tracking and anchor expansion.
- **[How Retrieval Works](how-retrieval-works.md)** — Step-by-step query execution trace.
