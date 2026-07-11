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
        description="Specific, non-obvious behavioral observations about who this user IS, or biographical/relational facts. No limits. FORMAT for behavioral patterns: Always anchor to a specific domain or subject — never a floating trait. Minimum 2 sentences: (1) the pattern with domain context, (2) concrete example from this conversation. Write behavioral patterns in third-person present tense: 'Approaches X by...' / 'Tends to...' — but biographical/relational facts can use past tense ('met at', 'works at'). FORMAT for biographical/relational facts: State the fact directly with all specifics — name, age, role, employer, how/where/when they met, relationship to the user. BAD: 'Prefers step-by-step explanations.' / 'Met Samantha.' GOOD: 'The user met Samantha (37 years old, real estate agent at EmlakPro) at a conference in 2021.'"
    )
    
    fact: List[str] = Field(
        default_factory=list,
        description="Context-specific knowledge from the LLM response — tied to THIS user's project, errors, or outcome. No limits. FORMAT: Start with the domain/concept name, give the complete finding with all specific values/names/identifiers, then state why it matters. Minimum 2-3 sentences — a reader seeing only this entry must know what topic it is and what the fact is. BAD: 'LLM corrected an error.' (empty). GOOD: 'In [domain], the user incorrectly believed [X]. The correct answer is [Y] because [reason].' Skip general textbook knowledge any LLM already knows. HARD BAN: never start with 'The user explored/asked/requested...' — route to [user]. Exception: biographical and relational facts belong in [user], not [fact]. Compress all facts about one concept into ONE entry."
    )
    
    epis: List[str] = Field(
        default_factory=list,
        description="Episodic context notes. No limits. Create as many distinct entries as needed to fully capture each distinct episode, event, or interaction. FORMAT: Write as a full narrative paragraph naming the domain and specific concept. Include what the user asked, what confused them, what corrections were made, and what a future session should expect. Include specific product names, values, dates, or identifiers from the conversation. Never a one-sentence summary. BAD: 'User asked about mattresses.' GOOD: 'The user asked about buying a new mattress (SleepWell Deluxe, 3,800 TRY) and whether the 5-year warranty extension was worthwhile. The discussion covered warranty coverage (manufacturing defects, structural issues), exclusions (stains, normal wear), and claim options (repair/replacement/refund). Future sessions should proactively include warranty details for sleep purchase queries.'"
    )

    decision: List[str] = Field(
        default_factory=list,
        description="Only meaningful choices with lasting impact on future interactions. One-sentence decisions are NEVER acceptable. FORMAT: Must contain all four elements — (1) domain/context the decision applies to, (2) the exact choice or directive, (3) full operational detail a future session needs to act on it, (4) what prompted it. BAD: 'Decided to buy the SleepWell Deluxe.' / 'Always mention warranty.' GOOD: '[Domain]: when this user asks about [topic], always [specific action with full detail — names, values, procedures]. This was established because [reason/origin].'"
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