from typing import List, Literal
from pydantic import BaseModel, Field

# --- 1. THE THINKING LAYER (Reasoning) ---
class ThinkingStep(BaseModel):
    txt: str = Field(..., description="Your reasoning thought — what you observed or concluded.")
    tag: Literal["user", "fact", "epis", "decision"] = Field(..., description="Which memory type this thought relates to.")
    topics: List[str] = Field(..., description="The topic path this thought belongs to, e.g. ['CS50x', 'Tideman'].")

    class Config:
        extra = "forbid"

# --- 2. THE MEMORY BUCKETS (The "Brain") ---
class MemoryData(BaseModel):
    topics_branch: List[str] = Field(
        default_factory=list,
        description="The sub-path within topics_root for this bucket, e.g. ['Tideman', 'lock_pairs']. Combined with topics_root gives the full tree path. Each level must add real specificity — no methodology names (Debugging, Mathematics) or meta-nodes (Interests, General X)."
    )
    
    user: List[str] = Field(
        default_factory=list, 
        description="Specific, non-obvious behavioral observations. Max 1 per job; most jobs produce zero. Write in third-person behavioral present tense: 'Approaches X by...' or 'Tends to...' — NEVER 'User asked/explored/requested...'. Store: behavioral patterns, recurring confusions, specific skills with context, completed courses WITH what they covered. Skip: generic interests, bare credentials without content. Prefer placing in same bucket as related fact, but standalone background credentials are acceptable."
    )
    
    fact: List[str] = Field(
        default_factory=list, 
        description="Context-specific knowledge from the LLM response — tied to THIS user's project, errors, or outcome. Max 2-3 total across ALL buckets per job. Skip general textbook knowledge any LLM already knows. Compress all facts about one concept into ONE entry. HARD BAN: never write 'The user explored/asked/requested/wanted/tried...' inside a fact — that is a user-action description; route it to [user] instead."
    )
    
    epis: List[str] = Field(
        default_factory=list, 
        description="Exactly ONE entry per bucket covering the FULL conversation arc for that topic — never one per message turn. Future-facing context note: if this topic comes up again, what matters about how this user engaged with it? Cover what was confusing, what corrections were made, how it was resolved."
    )

    decision: List[str] = Field(
        default_factory=list, 
        description="Only impactful choices worth recalling in a future session."
    )

    class Config:
        extra = "forbid" 

# --- 3. THE MASTER OBJECT ---
class ChatExtraction(BaseModel):
    reasoning: List[ThinkingStep] = Field(
        ...,
        description="Think step by step before extracting. For each potential memory, reason about: (1) does it pass the extraction threshold? (2) which type is it — fact/epis/user/decision? (3) what topic path does it belong to? Use this to plan your memory buckets before writing them."
    )
    topics_root: List[str] = Field(
        ...,
        description="Shared top-level domain(s) for ALL buckets in this extraction, e.g. ['Computer Science'] or ['Mathematics']. Use 1-2 items max. If the conversation spans genuinely unrelated domains, set to [] and encode the full path in each bucket's topics_branch instead."
    )
    memory: List[MemoryData] = Field(
        ...,
        description="List of memory buckets. Each bucket groups related memories sharing the same topics_branch path. Prefer fewer, denser buckets over many shallow ones."
    )

    class Config:
        extra = "forbid"  # Matches additionalProperties: false