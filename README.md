# 🧠 MindCache

**An open-source long-term memory engine for LLM agents.**

Instead of storing conversations as a flat collection of embedding vectors, MindCache organizes them into a living knowledge hierarchy with specialized memory types and evolving decision tracking. It is built for assistants that need to reason across long-term interaction histories.

[![BEAM-1M](https://img.shields.io/badge/Benchmark-BEAM--1M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![BEAM-10M](https://img.shields.io/badge/Benchmark-BEAM--10M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue)](https://pypi.org/project/mindcache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Medium Blog](https://img.shields.io/badge/Read_Design_Blog-Medium-black?logo=medium)](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)
[![YouTube Demo](https://img.shields.io/badge/Watch_Demo-YouTube-red?logo=youtube)](https://www.youtube.com/watch?v=wcTQkyN1CoM)

---

👉 **[Read the Design Article](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)** &nbsp;|&nbsp; 🎥 **[Watch Demo Video](https://www.youtube.com/watch?v=wcTQkyN1CoM)** &nbsp;|&nbsp; ⚡ **[Quick Start](#-quick-start-30-seconds)**

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

## 💡 Why MindCache?

Most long-term memory systems treat past interactions as a flat pool of unstructured embedding vectors. Over weeks of interaction, flat retrieval suffers from **context inflation**, **temporal collapse** (treating old choices as equal to current decisions), and **broad-query failure** (unable to answer high-level questions like *"What projects have I worked on this month?"*).

### ⚔️ Why Not a Vector Database?

| Feature | Traditional Vector Stores | MindCache |
| :--- | :--- | :--- |
| **Memory Structure** | Flat independent embedding vectors | Living topic hierarchy tree |
| **Decision Lifecycle** | Treats old choices as equal to current decisions | Tracks decision state evolution (`Active` vs `Superseded`) |
| **Broad Queries** | Fails or returns random snippets | Maintains RAPTOR-style incremental summaries |
| **Search Engine** | Pure dense similarity | Hybrid Vector + BM25 + Decision-Anchor expansion |

- **Problem**: Flat vector stores return disconnected snippets without temporal or structural context.
- **Insight**: Long-term memory requires distinct memory types, explicit decision lifecycle tracking, and hierarchical summaries.
- **Design**: MindCache continuously organizes incoming information into a dynamic topic tree, tracks evolving decisions, and rolls up delta summaries.
- **Evidence**: Evaluated on 300 benchmark questions across 1M and 10M token context windows, MindCache achieved the best overall performance among the memory systems evaluated in our BEAM experiments.

### System Pipeline

```
Conversation Turns
      │
      ▼
Memory Extraction
      │
      ▼
Four Memory Types (User / Decision / Episodic / Knowledge)
      │
      ▼
Decision State Tracking
      │
      ▼
Living Hierarchical Topic Tree
      │
      ▼
Incremental Delta Summaries
      │
      ▼
Hybrid RRF Search (Vector + BM25)
      │
      ▼
Decision-Anchor BM25 Expansion
      │
      ▼
Final Assembled Context → LLM Prompt
```

### 🌿 Automatically Organized Topic Hierarchy

MindCache incrementally organizes memories into a living topic hierarchy. Leaf nodes store memory clusters while internal nodes organize concepts and power hierarchical retrieval and summarization.

<p align="center">
  <img src="https://github.com/user-attachments/assets/cc34f593-46c4-4369-b8ba-e2a16fca3f77" width="700" alt="Automatically Organized Topic Hierarchy" />
</p>

👉 **[Read complete Topic Hierarchy documentation](docs/topic-tree.md)**

---

## ✨ Highlights

- 🌲 **Living Hierarchical Topic Tree**: Organizes raw turns into dynamic knowledge trees rather than flat vector pools.
- 🧩 **Four Specialized Memory Types**: Dedicated handling for User, Decision, Episodic, and Knowledge memories.
- 🎯 **Decision State Tracking**: Tracks evolving choices so past decisions don't overwrite current preferences.
- 📚 **Incremental Hierarchical Summaries**: Bottom-up RAPTOR-style summaries for broad-topic and multi-session reasoning.
- ⚡ **1.08s Average Retrieval Latency**: Production-ready, low-latency execution.
- 📊 **Evaluated on BEAM QA**: Tested across 300 questions on 1M and 10M token context windows.

---

## 📊 Evaluation

MindCache was evaluated on the **BEAM QA Benchmark**—a long-term memory benchmark assessing retrieval performance across multi-session conversation histories at 1M and 10M token context windows (300 manually graded questions across 15 conversations).

- 📏 **300 Benchmark Questions** across 15 multi-session conversations
- ⏱️ **1.08s Average Retrieval Latency** for production queries
- 🏆 **Best overall performance** on BEAM QA benchmark

👉 **[View Full Evaluation & Benchmark Results](docs/evaluation.md)**

---

## ⚙️ Architecture Overview

The MindCache pipeline operates in three distinct phases:

### Phase 1 — Offline Ingestion Pipeline
Processes incoming conversation turns, filters noise, routes to existing topic paths, and extracts structured memories.

### Phase 2 — Background Dynamic Tree Lifecycle
Handles background leaf node splitting, sibling merging, Leiden graph partitioning, and incremental delta summaries.

### Phase 3 — Online Multi-Stage Hybrid Retrieval Engine
Executes vector + BM25 hybrid search, RRF rank fusion, query classification, and decision-anchor expansion in **1.08s average latency**.

👉 **[View Complete Architecture Specification](docs/architecture.md)** &nbsp;|&nbsp; 🔄 **[Trace End-to-End Retrieval Flow](docs/how-retrieval-works.md)**

---

## 🧠 Core Architectural Ideas

These design choices address common long-term memory failures such as context inflation, temporal collapse, and poor broad-query retrieval:

- 🧩 **Specialized Memory Types**: Separate buckets for User, Decision, Episodic, and Knowledge facts. 👉 **[Read more](docs/memory-types.md)**
- 📊 **Retrieval Budgeting**: Enforces explicit category quotas to prevent context inflation. 👉 **[Read more](docs/retrieval-budgeting.md)**
- 🌲 **Incremental Summaries**: Bottom-up RAPTOR-style rollups for broad overview queries. 👉 **[Read more](docs/summaries.md)**
- ⚖️ **Decision Anchors**: Uses active decisions to anchor BM25 lexical expansion queries. 👉 **[Read more](docs/decision-anchor-retrieval.md)**
- 🗂️ **Hierarchical Path Indexing**: Prepends complete tree paths to boost sparse lexical recall. 👉 **[Read more](docs/path-indexing.md)**

👉 **[View Core Architectural Ideas Guide](docs/design-decisions.md)**

---

## 🖼️ Visual Overview

Below are illustrations of how MindCache structures, updates, and retrieves memories across sessions.

### 1. Decision State Tracking
Tracks decision evolution, marking superseded choices to prevent outdated preferences from leaking. 👉 **[Read more](docs/decision-anchor-retrieval.md)**

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

---

### 2. Context Assembled for LLM
Structured context assembled for the LLM prompt, featuring memory type partitioning and decision anchors. 👉 **[Read more](docs/retrieval-budgeting.md)**

```
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

### 3. Incremental Summary Lifecycle
Rolls up node summaries from leaf memories bottom-up to support broad overview queries. 👉 **[Read more](docs/summaries.md)**

```
Leaf Memories Added (Session 1..N)
             ↓
    Delta Processing (Only new items)
             ↓
Parent Node Summary Updated (Incremental Rollup)
             ↓
Available for High-Level Summarization Queries
```

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
