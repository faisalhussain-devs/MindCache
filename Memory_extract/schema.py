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
        description="A list of long, comprehensive paragraphs. Each item should be a rich, multi-sentence text block grouping all related user profile details together."
    )
    
    fact: List[str] = Field(
        default_factory=list, 
        description="A list of LONG, cohesive paragraphs. DO NOT use short atomic sentences. Each item must be a fully developed, multi-sentence paragraph combining all related facts, names, numbers, and context into a single narrative block."
    )
    
    epis: List[str] = Field(
        default_factory=list, 
        description="A list of descriptive, contextual paragraphs summarizing the narrative of the conversation."
    )

    decision: List[str] = Field(
        default_factory=list, 
        description="A list of detailed paragraphs explaining the reasoning and context behind decisions or recommendations."
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