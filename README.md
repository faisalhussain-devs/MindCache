# 🧠 MindCache

**An open-source long-term memory engine for LLM agents.**

Instead of storing conversations as a flat collection of embedding vectors, MindCache organizes them into a living knowledge hierarchy with specialized memory types, evolving decision tracking, and incremental summaries. It is built for assistants that need to reason across weeks or months of conversations rather than retrieve isolated facts.

[![BEAM-1M](https://img.shields.io/badge/Benchmark-BEAM--1M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![BEAM-10M](https://img.shields.io/badge/Benchmark-BEAM--10M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue)](https://pypi.org/project/mindcache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Medium Blog](https://img.shields.io/badge/Read_Design_Blog-Medium-black?logo=medium)](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)

---

👉 **[Read the Design Article](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)** &nbsp;|&nbsp; 🎥 **Watch the Demo (Coming Soon)** &nbsp;|&nbsp; ⚡ **[Quick Start](#-quick-start-30-seconds)**

---

## 📹 Demo

*(45–60 second Demo GIF showing installation, adding conversations, queue processing, inspecting the topic hierarchy, and performing multi-session retrieval context assembly)*

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

MindCache shifts from **flat search** to a **living hierarchical memory tree**:

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

### 🌲 Automatically Organized Topic Hierarchy

As conversations are ingested, MindCache continuously organizes extracted memories into a hierarchical topic tree. Rather than storing memories as a flat collection of embeddings, related concepts are grouped into increasingly specific topics. Leaf nodes contain memory clusters (e.g. `[memories: 47]`), while internal nodes provide semantic organization for retrieval and summarization.

<img width="541" height="551" alt="Screenshot 2026-07-31 083954" src="https://github.com/user-attachments/assets/cc34f593-46c4-4369-b8ba-e2a16fca3f77" />


> *This hierarchy is maintained incrementally as new conversations arrive and serves as the structural backbone for both hierarchical summarization and hybrid retrieval.*

---

## ✨ Highlights

- 🧠 **Structured Long-Term Memory**: Organizes raw turns into living knowledge hierarchies rather than flat vector pools.
- 🏷️ **Four Memory Types**: Dedicated handling for User, Decision, Episodic, and Knowledge memories.
- ⚖️ **Decision State Tracking**: Tracks evolving choices so past decisions don't overwrite current preferences.
- ⚡ **Hybrid RRF Search**: Merges dense semantic vector search with sparse BM25 lexical matching via Reciprocal Rank Fusion.
- 📜 **Incremental Summaries**: Bottom-up RAPTOR-style summaries for broad-topic and multi-session reasoning.
- 🚀 **1.08s Retrieval Latency**: Production-ready, low-latency execution.
- 🧪 **BEAM Benchmark Evaluated**: Tested across 300 questions on 1M and 10M token context windows.

---

## 📊 Evaluation Summary

MindCache was evaluated on the **BEAM QA Benchmark**—a long-term memory benchmark designed to evaluate retrieval across multi-session conversation histories at 1M and 10M token context windows (300 manually graded questions across 15 conversations).

### Evaluation Overview

| Metric | Evaluation Setting / Result |
| :--- | :--- |
| **Benchmark** | BEAM QA Benchmark |
| **Evaluated Dataset** | 300 questions across 15 multi-session conversations |
| **Context Scale** | 1M and 10M token context windows |
| **Average Retrieval Latency** | **1.08 seconds** |
| **Hierarchical Summary Impact** | 6 activations / 5 improvements / 2 failure-to-pass conversions |

### Benchmark Evaluation Summary

| System | Experiment Results | Memory & Retrieval Architecture |
| :--- | :--- | :--- |
| **MindCache** | 🥇 **Best overall performance in our evaluation*** | Living topic hierarchy, decision tracking & incremental summaries |
| **Mem0** | 🥈 **Competitive baseline** | Flat Memory Store |

*\* Based on our evaluation of the BEAM benchmark (300 questions across 15 conversations). Full methodology and category breakdowns are described in the accompanying [design article](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian).*

---

### Category Performance Breakdown

- **Information Extraction** (`Mem0 ≈ MindCache`): Mem0 and MindCache performed comparably. MindCache is more conservative and refrains from hallucinating specifics when retrieval context is ambiguous.
- **Temporal Reasoning** (`Advantage: MindCache`): MindCache accurately reconstructs multi-month timelines, recovery schedules, and chronological event sequences.
- **Multi-session Reasoning** (`Advantage: MindCache`): MindCache excels at connecting memories across separate sessions, tracking how user preferences evolve over time.
- **Summarization** (`Advantage: MindCache`): During follow-up evaluation, hierarchical summaries were activated on 6/20 complex benchmark queries, improving performance on 5 of them and directly converting 2 previously failing cases into passing scores.
- **Robustness & Abstention** (`Advantage: MindCache`): When information is missing, MindCache explicitly abstains ("context does not contain this") rather than fabricating incorrect details (e.g., wrong dates or numbers).

---

### Strengths & Remaining Failure Modes

#### Key Strengths
- **Multi-session synthesis**: Seamlessly bridges facts across months of conversation history.
- **Chronological tracking**: Accurately tracks sequence of events and evolving preferences.
- **Broad topic coverage**: Hierarchical summaries answer high-level overview questions effectively.

#### Remaining Failure Modes
- **Summary compression**: Incremental summaries occasionally omit fine-grained named entities or specific numeric values.
- **Ambiguity resolution**: Cautious retrieval logic sometimes opts for uncertainty/abstention when multiple candidate memories overlap, costing points on strict exact-match benchmarks.
- **Fine-grained evidence loss**: Compressing long subtrees into high-level summaries can occasionally drop specific minor details required by exact-match test rubrics.

---

## 🔬 Key Architectural Findings

During the development and evaluation of MindCache, we experimented with multiple memory organization and retrieval strategies. **Five architectural decisions consistently emerged as valuable during development and were retained in the final system. One of these (hierarchical summaries) was quantitatively evaluated, while the remaining decisions are supported by repeated manual inspection and iterative testing.**

### 1. Specialized Memory Types
Separating memories into four semantic categories—**User**, **Decision**, **Episodic**, and **Knowledge**—became a core design decision after iterative experimentation, enabling specialized update lifecycles and more balanced retrieval.

### 2. Retrieval Budgeting Per Memory Type
Rather than filling the context window with the globally highest-scoring memories (which often results in a single memory category dominating), MindCache allocates explicit retrieval quotas across memory types. This guarantees evidence diversity in every prompt.

### 3. Dynamic Hierarchical Tree & Incremental Summaries
Incremental RAPTOR-style hierarchical summaries improved retrieval for broad multi-topic queries where semantic vector search alone often struggled. 
> **Evaluation Finding**: In direct evaluation across 20 complex benchmark queries, hierarchical summaries were triggered on 6 queries, positively impacting 5 of them and directly converting 2 previous failures into passing scores (specifically for summarization and temporal-ordering tasks).

### 4. Decision-Guided Retrieval (Decision Anchors)
Top-ranked Decision memories act as semantic anchors. MindCache extracts key concepts from active decisions and performs BM25 lexical expansion to pull in supporting Episodic and Knowledge memories that standard vector search can miss—especially when the user prompt contains few discriminative keywords.

### 5. Hierarchical Path Indexing
Stored memories index their complete tree path (e.g., `Artificial Intelligence → Machine Learning → Deep Learning → PyTorch`). This provides additional lexical context that can improve BM25 recall for broader conceptual queries.

### Architectural Findings Evidence

| Finding | Evaluation Evidence | Evidence Level |
| :--- | :--- | :--- |
| **Hierarchical summaries** | 6 activations, improved 5, converted 2 failures | 📊 **Quantitatively Supported** |
| **Decision anchors** | Manual retrieval analysis across development | 🔍 **Observed in Development** |
| **Hierarchical path indexing** | Manual inspection of retrieved evidence across representative queries | 🔍 **Observed in Development** |
| **Four memory types** | Architectural schema refinement | 🏗️ **Design Rationale** |
| **Memory type quotas** | Iterative architectural refinement | 🏗️ **Design Rationale** |

---

## 🖼️ Visual Overview & Screenshots

Below are illustrations of how MindCache structures, updates, and retrieves memories across sessions.

### 1. Decision State Tracking
Tracks decision evolution, marking superseded choices to prevent outdated preferences from leaking.
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
> *Decisions evolve over time rather than persisting as conflicting memories.*

---

### 2. Context Assembled for LLM
Final prompt context generated by MindCache, featuring memory type partitioning and decision anchors.
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
> *Structured context assembled for the LLM prompt.*

---

### 3. Incremental Summary Lifecycle
Rolls up node summaries from leaf memories bottom-up to support broad overview queries.
```
Leaf Memories Added (Session 1..N)
             ↓
    Delta Processing (Only new items)
             ↓
Parent Node Summary Updated (Incremental Rollup)
             ↓
Available for High-Level Summarization Queries
```
> *Hierarchical summaries support broad, multi-session overview queries.*

---

## 📦 Core Features

### 🌲 Memory Organization
- **Living Hierarchical Topic Tree**: Dynamically builds topic nodes to organize memories logically.
- **Dynamic Graph Restructuring**: Performs Leiden graph partitioning, leaf node splitting, and sibling merging as memory grows.
- **Incremental Delta Summaries**: Generates bottom-up summaries processing only new leaf entries (deltas) to save LLM tokens.

### 👤 Memory Modeling
- **Four Specialized Memory Types**: Segmented into *User*, *Decision*, *Episodic*, and *Knowledge* buckets.
- **Decision State Machine**: Automatically detects and manages decision states (`Active`, `Superseded`, `Conditional`, `Rejected`).
- **Grounded Routing**: Queries existing top paths as constraints before extraction to eliminate duplicate topic nodes.

### 🔎 Multi-Stage Retrieval Engine
- **Hybrid Vector + BM25 + RRF**: Parallel dense vector search and sparse lexical search merged via Reciprocal Rank Fusion.
- **Decision-Anchor Expansion**: High-scoring decisions act as expansion anchors to retrieve related episodic and knowledge context.
- **BM25 Morphological Aliasing**: Uses a Union-Find data structure with lemmatization to unify word inflections (`database` / `databases`) in-memory.
- **Adaptive Query Classification**: Dynamically routes between broad overview queries (pulling summaries) and specific factual queries.

---

## 🏗️ Architecture Overview

The MindCache pipeline operates in three distinct phases:

### Phase 1 — Offline Ingestion Pipeline
```mermaid
flowchart TD
    classDef dark      fill:#313244,color:#cdd6f4,stroke:#585b70
    classDef llm       fill:#cba6f7,color:#11111b,stroke:#11111b
    classDef tree      fill:#89dceb,color:#11111b,stroke:#11111b
    classDef yellow    fill:#f9e2af,color:#11111b,stroke:#11111b
    classDef green     fill:#a6e3a1,color:#11111b,stroke:#11111b
    classDef analyzer  fill:#f38ba8,color:#11111b,stroke:#11111b

    A["💬 Raw Turn"]:::dark --> B{"🤖 Worth Remembering?"}:::llm
    B -- No --> C["Ignore & Return"]:::dark
    B -- Yes --> D["🌲 Fetch Top-K Topic Paths"]:::tree
    D --> E["Smart Grounded Routing"]:::llm
    E --> F["🤖 Extract & Classify Memories"]:::llm
    F --> G["Save to Topic Tree Path"]:::tree
    G --> H{"Batch Complete?"}:::dark
    
    H -- Yes --> I["🔍 Analyze Decision States"]:::analyzer
    I --> J{"⏱️ Crossed REORG_THRESHOLD?"}:::yellow
    
    J -- No --> K["Refresh Cache & Persist"]:::green
    J -- Yes --> L["✂️ Reorganize Tree\n(Leiden Partitioning / Split / Merge)"]:::yellow
    L --> M{"Summarization Enabled?"}:::yellow
    M -- Yes --> N["📜 Generate Delta Summaries"]:::llm
    M -- No --> K
    N --> K
```
> *Phase 1 — Offline ingestion pipeline: queueing, grounded routing, extraction, decision analysis, and reorganization trigger.*

---

### Phase 2 — Background Dynamic Tree Lifecycle
```mermaid
flowchart LR
    classDef tree     fill:#cba6f7,color:#11111b,stroke:#11111b
    classDef reorg    fill:#f9e2af,color:#11111b,stroke:#11111b
    classDef summary  fill:#a6e3a1,color:#11111b,stroke:#11111b
    classDef trigger  fill:#313244,color:#cdd6f4,stroke:#585b70

    T["🌲 Dynamic Topic Tree\n(Hierarchical ontology)"]:::tree

    R1["✂️ Split overloaded leaf nodes"]:::reorg
    R2["🔗 Merge sparse sibling nodes"]:::reorg
    R3["⬆️ Promote / Demote paths"]:::reorg
    R4["🌐 Leiden Graph Partitioning"]:::reorg

    S["📜 Incremental Delta Summaries\n(Bottom-up delta rollups)"]:::summary

    TH["⏱️ Threshold Trigger\n(TriadBlock Count)"]:::trigger

    TH --> T
    T --> R1 & R2 & R3 & R4
    R1 & R2 & R3 & R4 --> T
    T -- "New leaf memories" --> S
    S -- "Summary stored at parent" --> T
```
> *Phase 2 — Background dynamic tree lifecycle: graph partitioning, node splits/merges, and incremental delta summarization.*

---

### Phase 3 — Online Multi-Stage Hybrid Retrieval Engine
```mermaid
flowchart TD
    classDef dark      fill:#313244,color:#cdd6f4,stroke:#585b70
    classDef llm       fill:#cba6f7,color:#11111b,stroke:#11111b
    classDef search    fill:#89b4fa,color:#11111b,stroke:#11111b
    classDef anchor    fill:#f38ba8,color:#11111b,stroke:#11111b
    classDef green     fill:#a6e3a1,color:#11111b,stroke:#11111b

    Q["🔎 User Query"]:::dark --> A["Context Bridge & Embed"]:::dark
    A --> B["Score (Vector + BM25)"]:::search
    B --> C["Reciprocal Rank Fusion RRF"]:::search
    
    C --> D["🏷️ Classify Query\n(Broad vs Fact / Temporal)"]:::llm
    
    D --> E["Partitioned Candidate Retrieval"]:::search
    E --> F["Memory Pools\n(User / Knowledge / Episodic / Decision)"]:::search
    D -->|"If Broad Query"| G["📜 Summaries Pool"]:::search
    
    F & G --> H["⚖️ Get Top 3 Decisions"]:::anchor
    H --> I["BM25 Anchor Expansion"]:::anchor
    I --> J["Deduplicate Candidates"]:::search
    
    F & J --> K["📋 Inject Directives\n(Profile, Recency, Abstention)"]:::anchor
    K --> OUT["📤 Final Context → LLM Prompt"]:::green
```
> *Phase 3 — Online multi-stage hybrid retrieval engine: RRF scoring, memory pool partitioning, decision-anchor expansion, and prompt assembly.*

---

## ⚠️ Current Limitations

- **Summary Compression**: Bottom-up node summaries can occasionally compress away fine-grained named entities or specific numeric values.
- **Ambiguity Resolution**: When multiple candidate memories closely match a query, retrieval logic errs on the side of caution/abstention rather than making a guess.
- **Automatic Summary Refinement**: Incremental delta updates work continuously, but full background re-summarization during major tree re-organizations is under active development.

---

## 🗺️ Roadmap

- [x] **Hierarchical Topic Tree**: Dynamic memory organization.
- [x] **Decision State Machine**: Tracking active vs superseded choices.
- [x] **Incremental Delta Summaries**: Token-efficient rollup summaries.
- [x] **BM25 Union-Find Aliasing**: Morphological term matching.
- [ ] **Utility-Aware Memories**: Automatic memory importance scoring based on access patterns.
- [ ] **Automatic Memory Aging & Decay**: Time-decay scoring for low-relevance episodic memories.
- [ ] **Multi-Agent Shared Memory**: Safe cross-agent memory partitions with capability-based access controls.

---

## ⚙️ Configuration & Database Setup

MindCache adapts to your environment automatically. It supports SQLite out-of-the-box for local development and PostgreSQL (with `pgvector`) for production scaling.

### Database Backend Setup

#### SQLite (Default)
- **Default**: Creates `mindcache.db` in the current working directory.
- **Custom Path**: Pass `db_path="path/to/my_memory.db"` to constructor or set `MINDCACHE_DB_PATH` environment variable.

#### PostgreSQL (with pgvector)
Ensure the `vector` extension is enabled on your PostgreSQL instance (`CREATE EXTENSION IF NOT EXISTS vector;`):
```bash
export MINDCACHE_DB_URL="postgresql://user:password@localhost:5432/my_database"
```
Or pass the URL directly to constructor:
```python
mc = MindCache(db_path="postgresql://user:password@localhost:5432/my_database")
```

### Configuration Options

| Setting | Type | Location | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enable_summarization` | `bool` | Client Constructor | `False` | Enables bottom-up delta summaries of topic tree nodes to support broad overview queries. |
| `use_reranker` | `bool` | `.search()` Method | `True` | Applies the ~600MB Jina v2 Cross-Encoder model. Set to `False` to use hybrid RRF ordering for **1.08s low-latency retrieval**. |

---

## 📡 API Reference

### Client Constructor

```python
mc = MindCache(
    db_path: str = "mindcache.db",      # SQLite path OR PostgreSQL connection URL
    gemini_api_key: str = None,         # API key (or set GEMINI_API_KEY env var)
    provider: str = "gemini",           # Provider: "gemini" | "openai" | "anthropic"
    model_name: str = "gemini-2.5-flash",
    enable_summarization: bool = False  # Enable incremental bottom-up summaries
)
```

### Methods

- **`add(messages: list[dict], user_id: str = "default") -> int`**: Buffers conversation turns into the ingestion queue. Returns Ingestion Job ID.
- **`process_queue(user_id: str = "default", limit: int = None) -> dict`**: Drains queue, extracts structured memories, updates decision states, and triggers tree reorganization/summaries.
- **`search(query: str, user_id: str = "default", top_k_corpus: int = 30, use_reranker: bool = True) -> str`**: Retrieves formatted context for LLM prompt injection. Set `use_reranker=False` for ~1.08s latency.
- **`get_all(user_id: str = "default", memory_type: str = None) -> list[dict]`**: Retrieves stored memories for a user, optionally filtered by `memory_type` (`user`, `knowledge`, `episodic`, `decision`).
- **`delete(memory_id: int, user_id: str = "default") -> bool`**: Deletes a specific memory entry by ID.
- **`reset(user_id: str = "default") -> None`**: Clears all stored data for a user.

---

## 🤝 Contributing

We welcome contributions! Please open an issue or submit a pull request on GitHub to help advance structured long-term memory for AI agents.

## 📄 License

MindCache is open-source software licensed under the **[MIT License](LICENSE)**.
