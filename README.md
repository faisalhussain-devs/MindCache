# 🧠 MindCache

**An open-source long-term memory engine for LLM agents.**

[![BEAM-1M](https://img.shields.io/badge/Benchmark-BEAM--1M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![BEAM-10M](https://img.shields.io/badge/Benchmark-BEAM--10M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue)](https://pypi.org/project/mindcache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Medium Blog](https://img.shields.io/badge/Read_Design_Blog-Medium-black?logo=medium)](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)
[![YouTube Demo](https://img.shields.io/badge/Watch_Demo-YouTube-red?logo=youtube)](https://www.youtube.com/watch?v=wcTQkyN1CoM)

---

**Agents forget.**

**More history doesn't mean better memory.**

As conversations grow, important information gets buried. An AI needs to know what to remember, what has changed, which decisions still matter, and what is relevant now.

Most systems treat this as a search problem.

**MindCache treats it as a memory problem.**

It turns conversations into organized, persistent memory and continuously updates that memory as the conversation evolves.

---

👉 **[Read the Design Article](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)** &nbsp;|&nbsp; 🎥 **[Watch Demo Video](https://www.youtube.com/watch?v=wcTQkyN1CoM)** &nbsp;|&nbsp; ⚡ **[Quick Start](#-quick-start-30-seconds)**

---

## 🧠 How MindCache Builds Memory

MindCache doesn't treat every message as equally important, and it doesn't leave memories as a flat collection.

It turns conversations into four kinds of memory:

* **Preferences** — what a person likes, dislikes, or prefers.
* **Decisions** — choices that can change over time.
* **Experiences** — things that happened in previous conversations.
* **Knowledge** — useful information learned along the way.

These memories are then placed into a **living topic tree** that organizes related information together. As new conversations arrive, MindCache can reorganize that structure, split growing topics, merge related ones, and build summaries of broader areas.

The result is not just a collection of stored memories. **It is an evolving map of what the agent has learned over time.**

```
Conversations
     │
     ▼
┌───────────────────────────┐
│        MindCache          │
│                           │
│  Preferences              │
│  Decisions                │
│  Experiences              │
│  Knowledge                │
│                           │
│       ┌─ Work             │
│       ├─ Projects         │
│       │   ├─ MindCache    │
│       │   └─ ML Project   │
│       ├─ Preferences      │
│       └─ Personal         │
└───────────────────────────┘
```

---

## 🎯 Memory changes. MindCache keeps up.

People change their minds. Projects change direction. Old decisions become irrelevant.

For example:

> “We use TensorFlow for model training.”

Later:

> “We switched to PyTorch.”

A simple store can keep both statements without explicitly representing which decision replaced the other.

MindCache tracks the state of decisions over time:

```text
Old decision
TensorFlow
    ↓
SUPERSEDED

New decision
PyTorch
    ↓
ACTIVE
```

MindCache keeps the old decision in the history, but knows that it is no longer the current choice. This lets MindCache distinguish **what used to be true from what is true now**, instead of treating every past statement as equally relevant.

---

## 📚 Memory needs context, not just facts.

Remembering individual things is useful. But over time, an agent also needs to understand how those things fit together.

For example:

```text
Machine Learning
├── PyTorch
├── NLP
├── Computer Vision
└── Handwriting Project
```

MindCache builds summaries at different levels of this topic structure.

So instead of storing only:

> “Built a handwriting model.”

it can maintain a broader understanding of:

> “The user has been working on machine learning, with a focus on PyTorch, NLP, and handwriting synthesis.”

As new memories arrive, these summaries are updated with the new information instead of rebuilding everything from scratch.

**This gives the agent both the details and the bigger picture.**

---

## 💡 How It Works Together: An End-to-End Example

Consider how an agent's memory evolves across weeks of interaction:

```text
User conversations over time

  Week 1: "I'm starting a new project and using TensorFlow for model training."
    │
  Week 4: "I'm running a few experiments with PyTorch."
    │
  Week 8: "I've switched completely to PyTorch for all model training."

                     │
                     ▼
                 MindCache
                     │
    ┌────────────────┴────────────────┐
    │                                 │
    ▼                                 ▼
Decision Lifecycle              Living Topic Tree & Summary
TensorFlow → SUPERSEDED         Machine Learning
PyTorch    → ACTIVE             └── Frameworks
                                    └── PyTorch

                                Summary: "User's ML stack has transitioned
                                from TensorFlow to PyTorch."
                     │
                     ▼
User query (Week 10): "What ML framework am I using?"
                     │
                     ▼
Retrieved Context: PyTorch (ACTIVE)
```

By maintaining decision states, topic hierarchy, and incremental summaries, MindCache supplies the LLM with the current decision (**PyTorch**) while keeping the superseded choice (**TensorFlow**) in history.

---

## 📹 Demo

A 2-minute walkthrough covering installation, ingestion, automatic hierarchy generation, and hybrid retrieval.

[![MindCache Demo Video](https://img.youtube.com/vi/wcTQkyN1CoM/maxresdefault.jpg)](https://www.youtube.com/watch?v=wcTQkyN1CoM)
> 🎥 **[Watch the MindCache Walkthrough & Demo on YouTube](https://www.youtube.com/watch?v=wcTQkyN1CoM)**

---

## 🚀 Quick Start (30 Seconds)

### 1. Install
```bash
pip install mindcache
```

### 2. Usage
```python
from mindcache import MindCache

# Initialize client (SQLite-backed by default)
mc = MindCache(
    db_path="my_memory.db",
    provider="gemini",
    model_name="gemini-2.5-flash"
)

# 1. Ingest conversation turns (buffered into processing queue)
job_id = mc.add([
    {"role": "user", "content": "I prefer Python and FastAPI for backend development, and Postgres for DB."},
    {"role": "assistant", "content": "Got it! I will remember your preference for Python, FastAPI, and Postgres."}
], user_id="alice")

# 2. Process pending queue (extracts memories, updates tree and indices)
mc.process_queue(user_id="alice")

# 3. Retrieve structured context for future prompts
context = mc.search("What is Alice's preferred database?", user_id="alice")
print(context)
```

---

## ⚡ What Makes MindCache Different?

| Problem | Simple Memory Approach | MindCache |
| :--- | :--- | :--- |
| **Different kinds of information** | Treats memories similarly | Separates preferences, decisions, experiences, and knowledge |
| **Changing decisions** | Keeps old and new information together | Tracks which decision replaced another |
| **Growing information** | Leaves memories in a flat collection | Builds a living topic structure |
| **Big-picture questions** | Relies on individual memories | Maintains incremental summaries across the topic hierarchy |

*Under the hood, MindCache combines this memory structure with hybrid search, decision-guided retrieval, and controlled context allocation.*

### Memory Pipeline

```
Conversations
      │
      ▼
Important Information Extracted
      │
      ▼
Four Kinds of Memory (Preferences / Decisions / Experiences / Knowledge)
      │
      ▼
Living Topic Structure
      │
      ▼
Changing Decisions Tracked
      │
      ▼
Broader Summaries
      │
      ▼
Relevant Context Assembled
      │
      ▼
LLM Prompt
```

---

## ⚙️ Architecture & Technical Retrieval

The MindCache pipeline operates in three distinct phases:

- **Phase 1 — Ingestion & Grounded Routing**: Filters noise, maps memories to existing topic paths, and extracts structured facts.
- **Phase 2 — Background Dynamic Tree Lifecycle**: Handles leaf node splitting, sibling merging, Leiden graph partitioning, and incremental delta summaries.
- **Phase 3 — Online Multi-Stage Hybrid Retrieval Engine**: Executes vector + BM25 hybrid search, RRF rank fusion, query classification, and decision-anchor expansion in **1.08s average latency**.

👉 **[View Complete Architecture Specification](docs/architecture.md)** &nbsp;|&nbsp; 🔄 **[Trace End-to-End Retrieval Flow](docs/how-retrieval-works.md)** &nbsp;|&nbsp; 🧠 **[Core Architectural Ideas](docs/design-decisions.md)**

---

## 📊 Evaluation

MindCache was evaluated on the **BEAM QA Benchmark**—a long-term memory benchmark assessing retrieval performance across multi-session conversation histories at 1M and 10M token context windows (300 manually graded questions across 15 conversations).

- 📏 **300 Benchmark Questions** across 15 multi-session conversations
- ⏱️ **1.08s Average Retrieval Latency** for production queries
- 🏆 **Best overall performance** on BEAM QA benchmark

👉 **[View Full Evaluation & Benchmark Results](docs/evaluation.md)**

---

## 📁 Project Structure

The project is organized into independent ingestion, storage, and retrieval modules.

```
mindcache/
├── client.py                # Main SDK interface & MindCache client
├── Database/                # Storage, tree reorg, embeddings & summaries
│   ├── db_manager.py        # Database operations (SQLite & pgvector)
│   ├── db_setup.py          # Table schema initialization
│   ├── decision_analyzer.py # Decision state tracking logic
│   ├── embedder.py          # Embedding generation & vector indexing
│   ├── nodes_summary.py     # Bottom-up RAPTOR delta summarization
│   └── reorganize_tree.py   # Tree splitting, merging & Leiden partitioning
├── Memory_extract/          # Ingestion & extraction pipeline
│   ├── input_denoiser.py    # Conversation noise filtering
│   ├── memory_extractor.py  # Structured fact & memory extraction
│   ├── safe_ai.py           # Provider LLM client wrapper (Gemini/OpenAI/Anthropic)
│   └── schema.py            # Pydantic schemas for memory types
└── retrieval/               # Online hybrid retrieval engine
    ├── active_path.py       # Does retrieval & Query classification & context assembly 
    ├── hybrid_search.py     # Hybrid vector + BM25 + RRF ranking
    └── root_cache.py        # Caches the nodes, memories and bm25 indexing
```

---

## ⚙️ Configuration & Database Setup

MindCache supports SQLite out-of-the-box and PostgreSQL (`pgvector`) for production workloads.

- ⚙️ **SQLite**: Default local setup (`mindcache.db`).
- 🐘 **PostgreSQL**: Set `MINDCACHE_DB_URL="postgresql://..."` or pass to `MindCache(db_path=...)`.

👉 **[View Full Configuration & Setup Guide](docs/configuration.md)**

---

## 📡 API Reference

- **`add(messages, user_id)`**: Buffer conversation turns into the ingestion queue.
- **`process_queue(user_id)`**: Drain queue, extract memories, and update tree summaries.
- **`search(query, user_id)`**: Retrieve formatted context for LLM prompt injection.
- **`get_all()`** / **`delete()`** / **`reset()`**: Manage stored user memories.

👉 **[View Complete API Reference](docs/API.md)**

---

## 🗺️ Roadmap

- [x] **Hierarchical Topic Tree**: Dynamic graph organization.
- [x] **Decision State Machine**: Evolving choice tracking.
- [x] **Incremental Delta Summaries**: Bottom-up rollup summaries.
- [ ] **Utility-Aware Memories**: Access-pattern importance scoring.
- [ ] **Automatic Memory Aging**: Time-decay scoring for episodic memories.

---

## 🤝 Contributing

We welcome contributions! Please open an issue or submit a pull request on GitHub to help advance structured long-term memory for AI agents.

## 📄 License

MindCache is open-source software licensed under the **[MIT License](LICENSE)**.
