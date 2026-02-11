from typing import List, Literal
from pydantic import BaseModel, Field

# --- 1. THE THINKING LAYER (Reasoning) ---
class ThinkingStep(BaseModel):
    txt: str = Field(...)
    tag: Literal["user", "fact", "epis", "decision"] = Field(...)
    topics: List[str] = Field(...)

    class Config:
        extra = "forbid"  # Matches additionalProperties: false

# --- 2. THE MEMORY BUCKETS (The "Brain") ---
class MemoryData(BaseModel):
    topics_branch: List[str] = Field(
        default_factory=list, 
    )
    
    user: List[str] = Field(
        default_factory=list, 
    )
    
    fact: List[str] = Field(
        default_factory=list, 
    )
    
    epis: List[str] = Field(
        default_factory=list, 
    )

    decision: List[str] = Field(
        default_factory=list, 
    )

    class Config:
        extra = "forbid" 

# --- 3. THE MASTER OBJECT ---
class ChatExtraction(BaseModel):
    reasoning: List[ThinkingStep] = Field(
        ..., 
    )
    topics_root: List[str] = Field(
        ..., 
    )
    memory: List[MemoryData] = Field(
        ..., 
    )

    class Config:
        extra = "forbid"  # Matches additionalProperties: false