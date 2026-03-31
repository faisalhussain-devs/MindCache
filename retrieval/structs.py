from dataclasses import dataclass, field
from typing import List, Optional, Any
from pydantic import BaseModel, Field

@dataclass
class RetrievalConfig:
    """Configuration for the Active Path Retrieval System"""
    # Context Bridge (Phase 1)
    drift_threshold: float = 0.5
    short_threshold_1: int = 200   # Max chars for current prompt to include n-1
    short_threshold_2: int = 300   # Max chars for (current+n-1) to include n-2
    
    # Root Search (Phase 2)
    root_threshold: float = 0.05
    
    # Root Descent (Phase 3)
    descent_threshold: float = 0.05
    top_k: int = 9  # Max candidates to send to refiner
    
    # Agentic Refiner (Phase 4)
    model_name: str = "qwen3-fast"

@dataclass
class RetrievalContext:
    """Holds the state for a single retrieval request"""
    # Raw Inputs
    current_prompt: str 
    last_user_msg: Optional[str] = None 
    prev_user_msg: Optional[str] = None 
    
    # Calculated
    query_text: str = "" 
    query_vector: Any = None 
    
    # Debug info
    drift_score: float = 0.0
    history_used: List[str] = field(default_factory=list) 

@dataclass
class CandidateTopic:
    """Single candidate from Root Descent"""
    name: str
    path: str              # "Backend > Database > Migrations"
    topic_id: int
    sim_score: float
    bm25_score: float
    timestamp: str         # Formatted timestamp
    is_leaf: bool

@dataclass
class RetrievalResult:
    """Final output from the retrieval system"""
    context: str = ""
    trace: dict = field(default_factory=dict)

# --- LLM OUTPUT SCHEMAS ---
class SelectedTopic(BaseModel):
    id: int = Field(..., description="Topic ID")
    chain: List[str] = Field(..., description="Topic path chain e.g. ['Backend', 'Database']")
    depth: str = Field(..., description="'summary' or 'leaf'")

class RefinedSelection(BaseModel):
    reasoning: str = Field(..., description="Analysis of what information is needed")
    selected_topics: List[SelectedTopic] = Field(..., description="Topics to retrieve with depth")
