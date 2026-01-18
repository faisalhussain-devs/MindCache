from typing import List, Literal
from pydantic import BaseModel, Field

# --- 1. THE THINKING LAYER (Reasoning) ---
class ThinkingStep(BaseModel):
    """
    Step-by-step analysis of the input.
    Forces the model to classify intent BEFORE generating memory.
    """
    txt: str = Field(
        ..., 
        description="Concise thought summary (<10 words)."
    )
    tag: Literal["user", "fact", "epis", "noise"] = Field(
        ..., 
        description="Classification: 'fact' now includes learned lessons and technical experience."
    )

    class Config:
        extra = "forbid"  # Matches additionalProperties: false

# --- 2. THE MEMORY BUCKETS (The "Brain") ---
class MemoryData(BaseModel):
    """
    The consolidated memory update payload.
    """
    topics: List[str] = Field(
        default_factory=list, 
        description="Keywords for indexing (e.g., 'CSS', 'Quantization')."
    )
    
    user: List[str] = Field(
        default_factory=list, 
        description="User preferences, bio, and behavioral instructions (e.g., 'Don't apologize')."
    )
    
    fact: List[str] = Field(
        default_factory=list, 
        description="Project knowledge, technical constraints, solutions that worked, and lessons learned from failures."
    )
    
    epis: List[str] = Field(
        default_factory=list, 
        description="Timeline of events (e.g., 'Tried X, failed, then tried Y')."
    )

    class Config:
        extra = "forbid"  # Matches additionalProperties: false

# --- 3. THE MASTER OBJECT ---
class ChatExtraction(BaseModel):
    """
    The final GSM output format.
    """
    reasoning: List[ThinkingStep] = Field(
        ..., 
        description="Step-by-step analysis of the input."
    )
    memory: MemoryData = Field(
        ..., 
        description="The consolidated memory update payload."
    )

    class Config:
        extra = "forbid"  # Matches additionalProperties: false