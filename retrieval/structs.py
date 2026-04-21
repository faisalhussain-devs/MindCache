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
    llm_choice_threshold: int = 7
    llm_max_selected: int = 6
    
    # Root Descent (Phase 3)
    top_k_vector: int = 100 # Max candidates after vector similarity
    top_k_rrf: int = 50 # Max candidates to send to cross encoder for reranking
    top_k_cross: int = 20 # Max candidates to send to refiner
    
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
    sub_queries: List[dict] = field(default_factory=list)  # List of {"text": str, "vector": Any, "roots": List[Topic]}
    
    # Debug info
    drift_score: float = 0.0
    history_used: List[str] = field(default_factory=list) 

@dataclass
class CandidateTopic:
    """Single candidate from Root Descent"""
    name: str
    path: str              # "Backend > Database > Migrations"
    topic_id: int
    cross_encoder_score: float
    rrf_score: float
    vector_rank: int
    bm25_rank: int
    timestamp_start: str    # Earliest memory in this node's subtree
    timestamp_end: str      # Latest  memory in this node's subtree
    is_leaf: bool

@dataclass
class RetrievalResult:
    """Final output from the retrieval system"""
    context: str = ""
    trace: dict = field(default_factory=dict)

# --- LLM OUTPUT SCHEMAS ---

# Phase 2: Query Intelligence output
class SubQuery(BaseModel):
    expanded_query: str = Field(..., description="Enriched focused sub-query with domain keywords, synonyms, and context.")
    selected_roots: List[str] = Field(..., description="Exact root names from the provided list relevant to THIS specific sub-query.")

class QueryIntelligence(BaseModel):
    """LLM analyzes the user query, determines intent, and breaks it down into 1 or more sub-queries if needed."""
    intent: str = Field(..., description="Query intent (RECALL, EXPLAIN, COMPARE, PLAN, or OVERVIEW)")
    queries: List[SubQuery] = Field(..., description="List of 1 or more focused sub-queries. Use 1 by default, Multiple ONLY if required.")

# Phase 4: Agentic Refiner output
class SelectedTopic(BaseModel):
    id: int = Field(..., description="Topic ID — can be the leaf ID OR any ancestor ID visible in the [id:N] prefixes of the path.")
    chain: List[str] = Field(..., description="Path chain of name segments for the chosen node e.g. ['Backend', 'Database']")
    depth: str = Field(..., description="'summary' or 'leaf'")

class RefinedSelection(BaseModel):
    reasoning: str = Field(..., description="Analysis of what information is needed")
    selected_topics: List[SelectedTopic] = Field(..., description="Topics to retrieve with depth")
