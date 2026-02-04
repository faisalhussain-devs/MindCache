from dataclasses import dataclass, field
from typing import List, Optional, Any
from pydantic import BaseModel, Field

@dataclass
class RetrievalConfig:
    """Configuration for the Active Path Retrieval System"""
    # Context Bridge (Phase 1)
    drift_threshold: float = 0.5
    max_msg_length: int = 2000 # Characters
    min_msg_length: int = 200
    
    # Root Dictionary Search (Phase 2)
    root_selection_threshold: float = 0.35 
    
    # Root Descent (Phase 3)
    threshold: float = 0.35 
    
    # Agentic Refiner (Phase 4)
    model_name: str = "qwen3-fast" # Model for refinement

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
class RetrievalResult:
    """Standardized output from the retrieval system"""
    candidates: str # The final candidates string

# --- LLM OUTPUT SCHEMA ---
class SelectedItem(BaseModel):
    topic_name: str
    reason: str

class RefinedSelection(BaseModel):
    reasoning: str = Field(..., description="Analysis of what information is needed")
    selected_items: List[SelectedItem] = Field(..., description="List of specific IDs to retrieve")
