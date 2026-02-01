from dataclasses import dataclass, field
from typing import List, Optional, Any

@dataclass
class RetrievalConfig:
    """Configuration for the Active Path Retrieval System"""
    # Context Bridge (Phase 1)
    drift_threshold: float = 0.5
    max_msg_length: int = 2000 # Characters. If msg > this, ignore history to avoid context rot/bloat.
    min_msg_length: int = 200
    
    # Sniper (Phase 2)
    root_selection_threshold: float = 0.35 # Minimum similarity to enter a topic
    
    # Beam Descent (Phase 3)
    hot_threshold: float = 0.5 # Load full summary
    cold_threshold: float = 0.35 # Load title/timestamp only
    # Below cold_threshold = Ignore

@dataclass
class RetrievalContext:
    """Holds the state for a single retrieval request"""
    # Raw Inputs
    current_prompt: str # n th msg 
    last_user_msg: Optional[str] = None # n-1 th msg
    prev_user_msg: Optional[str] = None # n-2 th msg
    
    # Calculated
    query_text: str = "" # The final text used for embedding
    query_vector: Any = None # Numpy array or bytes
    
    # Debug info
    drift_score: float = 0.0
    history_used: List[str] = field(default_factory=list) # Which msgs were used

@dataclass
class RetrievalResult:
    """Standardized output from the retrieval system"""
    context_str: str # The formatted text to inject into the LLM context
    sources: List[dict] # Metadata about where the info came from
    debug_log: List[str] # active path trace for transparency
