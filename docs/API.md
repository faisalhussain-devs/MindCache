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

## Core SDK Methods (6 Verbs)

### `add(messages: list[dict], user_id: str = "default") -> int`
Buffers conversation turns into the SQLite ingestion queue in milliseconds.
- **`messages`**: List of conversation turn dicts `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`.
- **`user_id`**: Scope for user/session identifier.
- **Returns**: Ingestion Job ID (`int`).

### `process(user_id: str = "default") -> dict`
Executes memory extraction on queued jobs, updates decision states, and refreshes the topic tree and summaries.
- **`user_id`**: User scope to process.
- **Returns**: Dictionary summary `{"success": int, "failed": int, "tree": dict}`.
- *(Backwards-compatible alias: `process_queue()`)*

### `search(query: str, user_id: str = "default") -> str`
Retrieves formatted structured context ready to inject into an LLM prompt.
- **`query`**: User question or search query.
- **`user_id`**: User scope.
- **Returns**: Formatted markdown context string containing user preferences, active decisions, episodic events, and knowledge.

### `inspect(user_id: str = "default", view: str = "memories", memory_type: str = None) -> list[dict] | dict`
Observability interface to inspect stored memory state.
- **`view`**: Inspection mode:
  - `"memories"` (default): Returns list of stored memory records.
  - `"tree"`: Clears old cache, builds fresh topic tree, and returns it.
  - `"all"`: Clears old cache, builds fresh topic tree, and returns `{"tree": tree, "memories": memories}`.
- **`memory_type`**: Optional filter when viewing memories (`"user"`, `"knowledge"`, `"episodic"`, `"decision"`).

### `forget(memory_id: int, user_id: str = "default") -> bool`
Removes a specific memory entry by ID for a user.
- **`memory_id`**: Registry ID of the memory to remove.
- **Returns**: `True` if deleted, `False` if not found.
- *(Backwards-compatible alias: `delete()`)*

### `reset(user_id: str = "default") -> None`
Clears all stored memories, topic nodes, and queued jobs for a user.

