# 📡 MindCache API Reference

Complete SDK reference for the `MindCache` Python client.

## Client Constructor

```python
from mindcache import MindCache

mc = MindCache(
    db_path: str = "mindcache.db",      # SQLite path OR PostgreSQL connection URL
    gemini_api_key: str = None,         # API key (or set GEMINI_API_KEY env var)
    provider: str = "gemini",           # Provider: "gemini" | "openai" | "anthropic"
    model_name: str = "gemini-2.5-flash",
    enable_summarization: bool = True   # Enable incremental bottom-up summaries (default: True)
)
```

### Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `db_path` | `str` | `"mindcache.db"` | Path to local SQLite file or PostgreSQL connection URL (`postgresql://...`). |
| `gemini_api_key` | `str` | `None` | API key for Google Gemini (optional if `GEMINI_API_KEY` environment variable is set). |
| `provider` | `str` | `"gemini"` | LLM provider (`"gemini"`, `"openai"`, `"anthropic"`). |
| `model_name` | `str` | `"gemini-2.5-flash"` | Specific LLM model identifier. |
| `enable_summarization` | `bool` | `True` | Builds bottom-up delta summaries across the topic tree. |

---

## SDK Methods

### `add(messages: list[dict], user_id: str = "default") -> int`
Buffers conversation turns into the ingestion queue.
- **`messages`**: List of conversation turn dicts `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`.
- **`user_id`**: User/session identifier.
- **Returns**: Ingestion Job ID (`int`).

### `process_queue(user_id: str = "default", limit: int = None) -> dict`
Drains the pending queue, extracts structured memories, updates decision states, and triggers tree reorganization/summaries.
- **Returns**: Execution summary dictionary.

### `search(query: str, user_id: str = "default", top_k_corpus: int = 30, use_reranker: bool = False) -> str`
Retrieves formatted structured context to inject into an LLM prompt.
- **`query`**: User question or prompt text.
- **`top_k_corpus`**: Number of memory candidates to retrieve.
- **`use_reranker`**: Set to `True` to enable Jina v2 Cross-Encoder reranking. Default is `False` for 1.08s average retrieval latency in our evaluation setup.

### `get_all(user_id: str = "default", memory_type: str = None) -> list[dict]`
Retrieves stored memories for a user, optionally filtered by `memory_type` (`"user"`, `"knowledge"`, `"episodic"`, `"decision"`).

### `delete(memory_id: int, user_id: str = "default") -> bool`
Deletes a specific memory entry by ID.

### `reset(user_id: str = "default") -> None`
Clears all stored data for a user.
