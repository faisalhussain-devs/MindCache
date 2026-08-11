from dataclasses import dataclass, field
from typing import List, Optional, Any

@dataclass
class RetrievalContext:
    """Holds the state for a single retrieval request"""
    # Raw Inputs
    current_prompt: str

    # Calculated
    query_text: str = ""
    query_vector: Any = None

@dataclass
class RetrievalResult:
    """Final output from the retrieval system"""
    context: str = ""
    trace: dict = field(default_factory=dict)
    # Query-type-specific system prompt snippet injected by the classifier
    system_hint: str = ""
    # The detected query type label for logging / eval
    query_type: str = ""

    def __str__(self) -> str:
        return self.context

