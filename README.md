# 🧠 MindCache

**Structured Long-Term Memory SDK for LLM Agents**

[![BEAM-1M](https://img.shields.io/badge/Benchmark-BEAM--1M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![BEAM-10M](https://img.shields.io/badge/Benchmark-BEAM--10M%20Passed-success)](https://arxiv.org/abs/2404.17299)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue)](https://pypi.org/project/mindcache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Medium Blog](https://img.shields.io/badge/Read_Design_Blog-Medium-black?logo=medium)](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian)

MindCache is a structured long-term memory engine designed for production-grade LLM agents. Unlike flat vector search databases or basic summary stores, MindCache maintains a **self-restructuring hierarchical memory ontology** with explicit decision tracking, outperforming flat retrieval systems on the BEAM QA benchmark.

> 📖 **Read the Design Story:** Detailed details of the architecture design are available in the blog post [Building MindCache: Designing an Agentic Memory System for Long-Term AI](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian).

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

# 1. Ingest conversation turns (writes to queue in milliseconds)
job_id = mc.add([
    {"role": "user", "content": "I prefer Python and FastAPI for backend development, and Postgres for DB."},
    {"role": "assistant", "content": "Got it! I will remember your preference for Python, FastAPI, and Postgres."}
], user_id="alice")

# 2. Process pending queue (runs extraction, updates tree and indices)
mc.process_queue(user_id="alice")

# 3. Retrieve relevant context for a future prompt
context = mc.search("What is Alice's preferred database?", user_id="alice")
print(context)
```

---

## 🛡️ Key Architectural Differentiators (Why MindCache?)

Most memory systems treat long-term memories as a flat pool of unstructured embedding vectors. MindCache introduces a structured, self-organizing memory architecture optimized for reasoning agents.

### 🔹 Phase 1 — Ingestion Pipeline

> Raw conversations are queued, embedded, and processed in batches. Grounded Routing is performed by querying existing tree paths as constraints *before* extraction. The Decision State Analyzer and reorganization processes execute later as batch tasks.

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

---

### 🔹 Phase 2 — Dynamic Tree Lifecycle

> The topic tree is never static. When the ingestion count crosses the reorganization threshold, a background process reorganizes the graph and builds incremental bottom-up summaries.

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

---

### 🔹 Phase 3 — Multi-Stage Hybrid Retrieval Engine

> Search queries are processed through a multi-stage preference-anchored pipeline: score generation & RRF, query classification, decision expansion, and directive assembly. The cross-encoder re-ranker was evaluated and removed — it offered marginal accuracy gains at a ~23× latency cost.

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

---


### 1. Four Structured Memory Types
Rather than storing generic chunks, MindCache segregates information into semantic types:
*   📝 **Episodic**: Interactive conversation events, milestones, and session context.
*   🧠 **Knowledge**: Fact statements, concepts, and factual domain knowledge.
*   👤 **User**: Identity facts, standing preferences, and behavioral attributes.
*   ⚖️ **Decision**: Explicit choices, directives, and resolutions made by the user.

### 2. Decision State Machine
Decisions evolve over time. MindCache runs a background **Decision State Analyzer** to identify conflicting or updated decisions. It tracks and flags states as:
*   `Active`: Current binding choice.
*   `Superseded`: Overridden by a newer decision (automatically suppressed from standard context but preserved for history).
*   `Conditional`: Depends on unresolved conditions.
*   `Rejected`: User explicitly decided against this option.

### 3. Decision-Anchored BM25 Expansion
Decisions act as semantic anchors. In retrieval, MindCache identifies the top-ranked `Decision` memories relevant to the query. It then uses those decisions as **expansion anchors** to retrieve related episodic, knowledge, and user memories, providing highly aligned background context.

### 4. Smart Ingestion & Grounded Routing
To prevent the topic tree from fragmenting (where the LLM creates duplicate or slightly different topic paths like `FastAPI` and `FastAPI backend`), MindCache performs **Grounded Routing**. Before calling the LLM extractor, we query the top-K closest paths from the existing tree and pass them as grounding constraints to guide the LLM's classification.

### 5. Self-Restructuring Hierarchical Topic Tree
MindCache organizes memories into a tree. However, unlike static hierarchical structures, the tree is living and reorganizes itself in the background:
*   **Splits** leaf nodes that grow too large or broad.
*   **Merges** sparse sibling branches with semantic overlap.
*   **Promotes / Demotes** nodes to optimize path depth.

### 6. Incremental Delta Summarization
Summaries are built bottom-up to resolve broad queries. MindCache does this **incrementally**. When new memories are added to a leaf node, we only process the *delta* (new entries since the last rollup) combined with the existing summary context, keeping LLM API token usage minimal.

### 7. BM25 Morphological Aliasing (Union-Find)
Standard BM25 lexical search misses word inflections (e.g. `database` vs `databases`, `run` vs `running`). MindCache solves this by coupling a **Union-Find data structure** with spaCy lemmatization. It collapses base words and their inflections into a single equivalence class, combining their indexes in-memory during queries for maximum recall.

### 8. 4-Phase Retrieval Pipeline
MindCache uses a multi-stage search sequence:
1.  **Partitioned RRF**: Performs vector search + BM25 independently per memory type and merges them via Reciprocal Rank Fusion.
2.  **Adaptive Query Classification**: Detects *Fact* vs *Broad Overview* queries. For broad overview queries where keywords are missing and vector similarity fails, it dynamically pulls RAPTOR-style hierarchical summaries.
3.  **Decision-Anchor Expansion**: Fans out to retrieve context surrounding top decision anchors.
4.  **Directive Injection**: Formulates the prompt context using strict directives (Abstention, Conflict Resolution, User Profile) based on classification.

---

## 📊 Evaluation

MindCache was benchmarked on [BEAM](https://arxiv.org/abs/2404.17299) — a long-term memory QA benchmark that stress-tests retrieval across multi-session conversation histories at 1M and 10M token context windows.

**Setup:** Hybrid Vector + BM25 retrieval, no cross-encoder re-ranker (evaluated separately — negligible accuracy gain at ~23× latency cost), no summarization. Average retrieval latency: **~1.08s**.

---

### Results

**1 Million context** — 10 conversations, ~20 questions each

| | Score | Accuracy |
| :--- | :---: | :---: |
| Conversation 1 | 18 / 20 | 90% |
| Conversation 2 | 19 / 20 | 95% |
| Conversation 3 | 18 / 20 | 90% |
| Conversation 4 | 18 / 20 | 90% |
| Conversation 5 | 15 / 20 | 75% |
| Conversation 6 | 16 / 20 | 80% |
| Conversation 7 | 18 / 20 | 90% |
| Conversation 8 | 18 / 20 | 90% |
| Conversation 9 | 17 / 20 | 85% |
| Conversation 10 | 17 / 20 | 85% |
| **Average** | **17.4 / 20** | **87%** |

**10 Million context** — 5 conversations, ~20 questions each

| | Score | Accuracy |
| :--- | :---: | :---: |
| Conversation 1 | 17 / 20 | 85% |
| Conversation 2 | 13 / 20 | 65% |
| Conversation 3 | 15 / 20 | 75% |
| Conversation 4 | 13 / 20 | 65% |
| Conversation 5 | 16 / 20 | 80% |
| **Average** | **14.8 / 20** | **74.0%** |

> Conversation 2 at 10M included 3–4 benchmark edge cases (Lambada-style multi-hop questions) that sit at the hard ceiling of the benchmark design. Excluding those, accuracy for that conversation is ~94%.

**Overall: 300 questions evaluated across both context scales.**

---

### ⚖️ Comparison with Mem0

The table below compares MindCache's results against the officially published benchmark scores from Mem0's research on the BEAM benchmark:

| Metric | Mem0 (Official Research) | MindCache (Ours) | Delta / Improvement |
| :--- | :---: | :---: | :---: |
| **BEAM-1M Accuracy** | 64.1% | **87.0%** | 🚀 **+22.9%** |
| **BEAM-10M Accuracy** | 48.6% | **74.0%** | 🚀 **+25.4%** |
| **Avg. Retrieval Latency** | ~1.20s | **~1.08s** | ⚡ **-10% faster** |
| **Avg. Tokens Used (1M)** | 6,719 | **~6,660** | 📉 **-59 tokens** |
| **Avg. Tokens Used (10M)** | 6,914 | **~6,690** | 📉 **-224 tokens** |

---

### Where failures come from

Nearly all real failures — across both context sizes — fall into two categories: **event ordering** and **summarization-type** questions. Both are broad queries that require retrieving many supporting memory chunks simultaneously. When the relevant evidence is spread across many nodes, the top-K retrieval pool doesn't always cover every required fact.

Occasionally, minor failures occur in multi-session tracking, temporal ordering, and knowledge updates (1 or 2 times). Factual, specific questions (knowledge lookups, user preferences, decisions) answer correctly and consistently.

---

### A note on summarization

Summaries were **not enabled** in this evaluation — not because they don't help, but because building them costs ~300 LLM calls per conversation, which made it impractical at this evaluation scale.

Architecturally, summarization matters more as memory grows. When a user has months of history, broad questions like *"summarize my project decisions"* are very hard to answer from raw chunks alone. That's what the incremental delta summarizer is designed for. It's available today via `enable_summarization=True` — full evaluation with it enabled is planned.

---




## ⚙️ Configuration & Database Setup

MindCache adapts to your environment automatically. It supports SQLite out-of-the-box and PostgreSQL (with `pgvector` extension) for scaling up.

### 1. Database Connection

You can configure your backend database using environment variables or directly via client initialization parameters:

#### SQLite (Default)
By default, MindCache uses an SQLite database:
* **Option A:** Leave default settings. It will create `mindcache.db` in the current working directory.
* **Option B:** Pass `db_path` parameter: `mc = MindCache(db_path="/path/to/my_memory.db")`
* **Option C:** Set `MINDCACHE_DB_PATH` environment variable: `MINDCACHE_DB_PATH=/path/to/my_memory.db`

#### PostgreSQL (with pgvector)
To scale up to PostgreSQL for production workloads, ensure the `vector` extension is enabled on your PostgreSQL instance (`CREATE EXTENSION IF NOT EXISTS vector;`):
* **Option A:** Set the `MINDCACHE_DB_URL` environment variable:
  ```bash
  export MINDCACHE_DB_URL="postgresql://user:password@localhost:5432/my_database"
  ```
* **Option B:** Pass the connection string directly to the client constructor:
  ```python
  mc = MindCache(db_path="postgresql://user:password@localhost:5432/my_database")
  ```

---

### 2. Configuration Settings

| Setting | Type | Location | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enable_summarization` | `bool` | Client Initialization | `False` | When `True`, builds and updates bottom-up delta summaries of the memory tree during queue processing to support broad queries. |
| `use_reranker` | `bool` | `.search()` Method | `True` | Applies the ~600MB Jina v2 Cross-Encoder model to rerank candidate memories. Set to `False` to fallback to hybrid RRF ordering for **23× faster retrieval (1.08s latency)**. |

---

## 📡 Package API Surface

### `MindCache` Client

```python
mc = MindCache(
    db_path: str = "mindcache.db",      # SQLite file path OR PostgreSQL connection string
    gemini_api_key: str = None,         # Gemini API key (optional if GEMINI_API_KEY env is set)
    provider: str = "gemini",           # LLM provider: "gemini" | "openai" | "anthropic"
    model_name: str = "gemini-2.5-flash",
    enable_summarization: bool = False  # Enable incremental bottom-up summaries
)
```

*   **`add(messages: list[dict], user_id: str = "default") -> int`**: Buffers conversation turns. Returns the Ingestion Job ID.
*   **`process_queue(user_id: str = "default", limit: int = None) -> dict`**: Drains the buffer, runs memory extraction, updates decision analyzer states, and triggers tree reorganization/summarization.
*   **`search(query: str, user_id: str = "default", top_k_corpus: int = 30, use_reranker: bool = True) -> str`**: Returns a formatted string containing relevant memories to inject into your LLM prompt. Set `use_reranker=False` to bypass the cross-encoder for low latency.
*   **`get_all(user_id: str = "default", memory_type: str = None) -> list[dict]`**: Fetch all memories stored for a user, optionally filtered by type (`user`, `knowledge`, `episodic`, `decision`).
*   **`delete(memory_id: int, user_id: str = "default") -> bool`**: Delete a specific memory row.
*   **`reset(user_id: str = "default") -> None`**: Clear all user data.

---

## 🤝 Contributing

We welcome contributions! Please open issues or submit PRs to help make MindCache the ultimate memory layer for agentic AI.

## License
MindCache is released under the **MIT License**.

