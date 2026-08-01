# ⚙️ Configuration & Database Setup Guide

MindCache supports SQLite out-of-the-box for local development and PostgreSQL (with `pgvector`) for production workloads.

## Database Backend Setup

### 1. SQLite (Default)
By default, MindCache creates `mindcache.db` in the current working directory.

- **Option A (Default)**: Leave default settings.
- **Option B (Constructor)**: `mc = MindCache(db_path="/path/to/my_memory.db")`
- **Option C (Environment Variable)**: `export MINDCACHE_DB_PATH="/path/to/my_memory.db"`

### 2. PostgreSQL (with pgvector)
To scale up to PostgreSQL for production workloads, ensure the `vector` extension is enabled on your PostgreSQL instance (`CREATE EXTENSION IF NOT EXISTS vector;`):

- **Option A (Environment Variable)**:
  ```bash
  export MINDCACHE_DB_URL="postgresql://user:password@localhost:5432/my_database"
  ```
- **Option B (Constructor Connection String)**:
  ```python
  mc = MindCache(db_path="postgresql://user:password@localhost:5432/my_database")
  ```

---

## Configuration Settings

| Setting | Type | Location | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enable_summarization` | `bool` | Client Constructor | `True` | Builds bottom-up delta summaries of topic tree nodes to support broad overview queries. |
| `use_reranker` | `bool` | `.search()` Method | `False` | Applies hybrid RRF ordering for **1.08s low-latency retrieval**. Set to `True` to enable the ~600MB Jina v2 Cross-Encoder model. |
