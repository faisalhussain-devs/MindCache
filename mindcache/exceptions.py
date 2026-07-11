class MindCacheError(Exception):
    """Base exception for all MindCache SDK errors."""
    pass

class IngestionError(MindCacheError):
    """Raised when memory ingestion, queue processing, or database save fails."""
    pass

class RetrievalError(MindCacheError):
    """Raised when context retrieval, search, or cache builds fail."""
    pass

class ConfigurationError(MindCacheError):
    """Raised when configuration, database path, or parameters are invalid."""
    pass

class ProviderError(MindCacheError):
    """Raised when LLM providers (Gemini, LiteLLM, OpenAI) return errors or exhaust quotas."""
    pass
