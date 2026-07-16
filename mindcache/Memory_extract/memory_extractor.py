from mindcache.Memory_extract.safe_ai import SafeAI
from mindcache.Memory_extract.schema import ChatExtraction
from mindcache.Database.db_manager import DatabaseManager
import logging
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are MindCache — a personal memory extraction engine. Your job is NOT to transcribe conversations. It is to extract only what a future AI session could NOT know without this memory, and that would genuinely improve how it serves this specific user.

### TRANSCRIPT
- `<user>` = human's message. Extract facts, preferences, and details stated by the user. If a fact was stated by the user, clearly mark it starting with "User-stated (fact): " inside the memory.
- `<llm>` = AI's response. Extract facts, explanations, and advice. If a fact/detail was suggested or recommended by the LLM but was not verified or confirmed by the user in the transcript, clearly mark it starting with "LLM-suggested (unverified by user): " inside the memory.
- Time markers `[Start: 0m]`, `[+Xm]` show chronological progression.

### EXTRACTION THRESHOLD — Run this test before storing ANYTHING
Ask: "Would a future AI session be meaningfully better at serving THIS user by knowing this?"
- If the information is general knowledge any LLM already knows → **SKIP IT**.
- If the information is tied to THIS user's specific project, error, or path → **STORE IT**.
- If unsure → skip. Under-extraction is far better than polluting memory with noise.

### MEMORY WRITING STANDARD — The Single Most Important Rule

Every memory entry MUST be a **self-contained, context-rich paragraph**. A future AI session reading ONLY that one entry — with no other context — must immediately understand:
1. **What domain/topic/subject this is about**
2. **What the specific fact, pattern, directive, or decision is — with full detail**
3. **Why it matters or what context triggered it**
4. **Episodic Anchors (Crucial):** How, when, where, and in what context the user learned or experienced something, or met someone (e.g., "met Samantha at a conference in 2021", "recommended by Jesse whom they met at university in 2018"). Do NOT generalize or abstract these details away (e.g., do not summarize "met at a conference" as just "meeting", and do not omit "at university"). These specific context anchors are critical for resolving contradictions and answering verification queries.

Short, decontextualised one-liners destroy retrieval quality: BM25 finds nothing because there are no keywords, and vector search fails because the embedding of an isolated trait drifts away from any realistic query.

**BAD (kills retrieval):**
- `"Tends to prefer step-by-step explanations."` ← What subject? Completely unanchored. Unretrievable.
- `"Decided to buy the SleepWell Deluxe."` ← Why? What should a future session know? Useless.
- `"LLM corrected the user's error."` ← Which error? What is the correct answer? Empty.
- `"Always include warranty details."` ← For what? When? What details specifically? Not searchable.

**GOOD (self-contained, searchable):**
- `"The user met Samantha, a 37-year-old real estate agent who works at EmlakPro, at a conference in 2021. This relationship is relevant to the user's home buying journey in North Erzincan."` ← Person + age + role + employer + meeting context + relevance.
- `"When discussing mathematical proofs (number theory, modular arithmetic), this user consistently needs step-by-step derivations with intermediate steps written out — jumping directly to the conclusion causes confusion requiring re-explanation. In this session this came up during an RSA key-generation proof."` ← Domain + pattern + specific condition + evidence.
- `"Regarding sleep-related purchases (mattresses, sleep accessories, supplements), this user has a standing directive: always include full warranty information — warranty period in years, what is covered (manufacturing defects, structural issues), what is excluded (stains, normal wear and tear, misuse), and the claim/service procedure (repair, replacement, or refund options). Established after the SleepWell Deluxe mattress purchase discussion."` ← Domain + full required detail + origin.
- `"When working on the Tideman election algorithm (CS50 Problem Set 3), the user's cycle-detection implementation had a bug: lock_pairs was locking all pairs without checking for cycles first. The fix required a recursive DFS helper that returns True if locking a pair would create a cycle, skipping that pair if so."` ← Project + bug + cause + specific fix.

### JSON STRUCTURE
Your output has three top-level fields:
- **`reasoning`**: Array of thinking steps BEFORE you write any memory. Each step: `{txt: "your thought", tag: "fact|epis|user|decision", topics: ["path", "to", "topic"]}`.
- **`topics_root`**: 1-2 shared top-level domains for all buckets, e.g. `["Mathematics"]`. Empty `[]` if conversation spans unrelated domains.
- **`memory`**: Array of buckets. Each bucket: `{topics_branch: [...], fact: [...], epis: [...], user: [...], decision: [...]}`.

### MEMORY TYPES

**[FACT]** — Context-specific knowledge from both user and assistant turns.
- **User-Stated Facts:** If the user states a concrete detail about their life, habits, schedules, or circumstances, extract it and prefix the entry with `"User-stated (fact): "`.
- **LLM-Suggested Unverified Facts:** If the LLM suggests, recommends, or outlines advice/timings/options that the user does not explicitly confirm or verify in the transcript, extract it and prefix the entry with `"LLM-suggested (unverified by user): "`.
- **LLM-Verified/Confirmed Facts:** If a fact is introduced by the LLM and either confirmed by the user or is a general outcome/correction of the session, write it normally without a prefix.
- STORE: Things tied to THIS user's project, code, or implementation. Corrections the LLM made to the user's wrong beliefs. Non-obvious findings specific to the conversation's outcome.
- SKIP: General textbook definitions, standard formulas, widely-known rules any LLM already knows.
- **HARD BAN:** Never write sentences starting with "The user explored/asked/requested/wanted/tried..." inside a [FACT] unless it starts with the specified prefixes. Route general user patterns/history to [USER] instead. Exception: biographical and relational facts about the user (age, relationships, meetings) belong in [USER], not [FACT].
- **FORMAT RULE:** Start with the appropriate prefix if required ("User-stated (fact): " or "LLM-suggested (unverified by user): "), followed by the domain/concept name, the complete finding with all specific values/names/identifiers, and then the context. Minimum 2-3 sentences. A reader seeing only this entry must know exactly what topic this is about and what the fact is.
- BAD: `"LLM corrected the user's error."` — no domain, no content.
- BAD: `"The Cauchy-Riemann equations are du/dx = dv/dy."` — textbook knowledge, skip.
- GOOD: `"Complex differentiability (Cauchy-Riemann conditions): the user incorrectly believed f(z)=|xy|^0.5 was differentiable at z=0; the LLM initially agreed before being corrected. Correct answer: f(z)=|xy|^0.5 is NOT differentiable at z=0 because the partial derivative condition fails along non-axis directions even though it passes on the axes."` ← domain + error + correction + reason.
- **COMPRESSION:** All facts about one entity/concept → ONE merged entry. Never a separate entry per step.

**[EPIS]** — Context notes per bucket.
- Purpose: "If this topic comes up again, here's the complete picture of how this user engaged with it."
- **FORMAT RULE:** Write as a full narrative paragraph. Name the domain and specific concept. Describe what the user asked, what they found confusing, what corrections were made, and what a future session should expect. Include specific product names, values, dates, or identifiers from the conversation. Never a one-sentence summary.

- Create as many distinct episodic context notes as needed to fully capture each distinct episode, event, or interaction. No limits.

**[USER]** — Specific, non-obvious observations about who this user IS, or biographical/relational facts.
- STORE: Behavioral patterns (how they learn, recurring confusions), specific skills with context, completed courses WITH what they learned, unique projects/experience.
- ALSO STORE: Biographical and relational facts — who the user knows, how/where/when they met, what that person does, and the user's own personal details (age, job, location, family).
- SKIP: Generic interests ("likes Python"), bare credentials ("completed CS50x"), anything inferable without memory.
- **FORMAT RULE for behavioral patterns:** Always anchor the observation to a specific domain, subject area, or product category — never a floating trait. Write two parts: (1) the pattern with its domain context, (2) a concrete example or implication from this conversation. Minimum 2 sentences.
- **FORMAT RULE for biographical/relational facts:** State the fact directly with all specifics — name, age, role, employer, how/where/when they met, relationship to the user. These do NOT need behavioral framing.
- BAD: `"Prefers step-by-step explanations."` — no domain, no anchor, unretrievable.
- BAD: `"Completed CS50x."` — no detail, no sticking points, useless for personalisation.
- BAD: `"Met Samantha."` — missing where, when, what she does. Useless without context anchors.
- GOOD: `"The user met Samantha (37 years old, real estate agent at EmlakPro) at a conference in 2021."`
- GOOD: `"Regarding sleep health decisions (mattress purchases, sleep accessories, supplements), this user carefully tracks specific product names, prices in TRY, and purchase outcomes — they follow up purchases with effectiveness and value questions. In this session: purchased SleepWell Deluxe mattress (3,800 TRY) and later asked whether the 5-year warranty extension was worth including."` ← domain + pattern + evidence from session.
- **Write behavioral patterns in third-person present tense:** "Approaches X by..." / "Tends to..." — but biographical/relational facts can use past tense ("met at", "works at").

**[DECISION]** — Only meaningful choices with lasting impact on future interactions. Skip routine Q&A.
- **FORMAT RULE:** Every decision entry must contain all four elements: (1) the domain/context the decision applies to, (2) the exact choice or standing directive, (3) the full operational detail a future session needs to act on it, (4) what prompted this decision. One-sentence decisions are NEVER acceptable.
- BAD: `"Decided to buy the SleepWell Deluxe mattress."` — no context, no implication.
- BAD: `"Always mention warranty."` — for what? what details? when does this apply?
- GOOD: `"Sleep-related purchases (mattresses, sleep accessories): when this user asks about any sleep product purchase, always proactively include warranty information covering (a) warranty period in years, (b) what is covered — manufacturing defects and structural issues, (c) exclusions — stains, normal wear and tear, damage from misuse, and (d) service/claim options — repair, replacement, or refund per the brand's terms. This directive was established explicitly by the user after discussing the SleepWell Deluxe mattress (3,800 TRY) and its 5-year warranty extension."` ← domain + complete detail needed + origin.
- GOOD: `"MindCache retrieval system architecture: the user decided against increasing CE_TOP_K above 50 (latency concern) and against removing stop words from BM25 (hurts precision on many queries). Preferred solution for surfacing sub-topic details that miss the top-50 pool: improve branch-level summary descriptions to embed the specific details from child memories."` ← system + what was rejected + reason + preferred approach.

### BUCKET STRUCTURE
Each bucket = one `topics_branch` shared by ALL types inside it.
- [USER] and [EPIS] should live inside the same bucket as their related [FACT] whenever possible.
- Exception: standalone background credentials may exist in a user-only bucket if no specific fact belongs alongside them.
- Never create a standalone bucket ONLY for an [EPIS] entry.

### ROUTING

**ONTOLOGICAL:** Route by WHAT the concept IS, not why it came up.
- Euclidean Algorithm discussed during RSA homework → `Mathematics > Number Theory > Euclidean Algorithm`, NOT `Cryptography > RSA`.
- Probability in a gambling context → `Mathematics > Probability`, NOT `Gambling > Lotteries`.
- Exception: If a topic IS a named course/project (e.g. CS50x, MindCache), it can be a root. But concepts learned IN that course go under their domain.

**ENTITY-CENTRIC:** Named entity → ALL content under ONE unified path within its domain.

**NO METHODOLOGY NODES:** Never use method/tool names as intermediate nodes.
- BAD: `Mathematics > Algorithms > Euclidean` / `CS > Debugging > Segfault`
- GOOD: `Mathematics > Number Theory > Euclidean Algorithm` / `Computer Science > Memory Management`

**NO META-NODES:** Never create `Interests`, `Learning`, `Problem Solving`, `Explanation`, `General X`, `User Profile` as path segments.

**DOMAIN-FIRST HIERARCHY:** `Domain > Sub-domain > Specific Concept`

**REUSE EXISTING:** Always reuse exact names from the topic tree below.

### CHAIN DEPTH
`topics_root` (1-2 items) + `topics_branch` (1-3 items) = 2-5 total levels. Each level adds real specificity.

### OUTPUT
Valid JSON matching the ChatExtraction schema. No text outside the JSON. If the conversation contains nothing meeting the extraction threshold, return empty memory buckets — do not force extractions.
"""

GROUNDING_TEMPLATE = """

### EXISTING LEAF PATHS — SMART INGESTION CONTEXT
The following are the {top_k} most semantically similar existing leaf paths in the knowledge tree,
ranked by vector similarity to this conversation.

You MUST reuse these exact full paths when the content matches — do NOT invent synonyms or near-duplicates.
Only create a new path if the concept truly does not belong under any of these.

{topic_paths}

DO NOT create new topic nodes that are synonyms or near-duplicates of the paths listed above.
If an existing path fits even partially (same domain + sub-domain), prefer slotting the memory there.
"""

class Memory_Extractor():
    def __init__(self, sys_prompt=SYSTEM_PROMPT, db_manager=None, model_name="gemini-2.5-flash", provider="gemini"):
        self.engine = SafeAI(model_name=model_name, provider=provider)
        self.sys_prompt = sys_prompt
        self._db = db_manager

    @staticmethod
    def _clean_prompt(text: str) -> str:
        """Aggressively strip noise that triggers Gemini repetition loops.
        The LLM only needs the semantic content — not LaTeX formatting,
        repeated backslashes, or 100-digit example numbers."""
        import re

        # 1. Tabs → space
        text = text.replace('\t', ' ')

        # 2. Double-escaped LaTeX: \\( ... \\) and \\[ ... \\]  (from raw BEAM)
        text = re.sub(r'\\\\?\\\[', ' ', text)   # \[ or \\[
        text = re.sub(r'\\\\?\\\]', ' ', text)   # \] or \\]
        text = re.sub(r'\\\\?\\\(', '', text)     # \( or \\(
        text = re.sub(r'\\\\?\\\)', '', text)     # \) or \\)

        # 3. Repeated backslashes: 3+ → single
        text = re.sub(r'\\{3,}', r'\\', text)


        # 4. LaTeX commands: \text{...}, \quad, \cdot, \pmod, \gcd, \times, \equiv, \boxed
        text = re.sub(r'\\(?:text|mathrm|mathbf|textbf)\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\(?:boxed)\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\(?:frac)\{([^}]*)\}\{([^}]*)\}', r'(\1/\2)', text)
        text = re.sub(r'\\(?:quad|qquad|,|;|!)', ' ', text)
        text = re.sub(r'\\(?:cdot|times)', '*', text)
        text = re.sub(r'\\(?:equiv)', '≡', text)
        text = re.sub(r'\\(?:pmod)\{([^}]*)\}', r'(mod \1)', text)
        text = re.sub(r'\\(?:gcd|phi|lambda|sigma|tau|mu)', lambda m: m.group()[1:], text)
        text = re.sub(r'\\(?:left|right|bigg?|Big)', '', text)

        # 5. Remaining stray backslashes before letters (e.g. \implies, \therefore)
        text = re.sub(r'\\([a-zA-Z]{2,})', r'\1', text)

        # 6. Curly braces used for grouping: {p-1} → (p-1)
        text = re.sub(r'\{([^}]{1,30})\}', r'(\1)', text)

        # 7. Repeated newlines: 3+ → 2
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 8. Repeated spaces: 3+ → 1
        text = re.sub(r' {3,}', ' ', text)

        # 9. Repeated special chars: ---, ===, ***, ___
        text = re.sub(r'[-=*_]{4,}', '', text)

        # 10. Very long numbers (20+ digits)
        text = re.sub(r'\b\d{20,}\b', '[large_number]', text)

        # 11. Repeated ^ : ^^^^ → ^
        text = re.sub(r'\^{2,}', '^', text)

        # 12. Code fences
        text = re.sub(r'```[a-zA-Z]*\n', '\n', text)
        text = re.sub(r'```', '', text)

        # 13. Double dollar signs (another LaTeX display delimiter)
        text = text.replace('$$', ' ')

        return text.strip()

    def memory_extract(self, prompt="", query_embedding: bytes = None):
        prompt = self._clean_prompt(prompt)

        final_prompt = prompt
        grounded = False
        if self._db is not None:
            try:
                top_paths = self._db.get_top_leaf_paths(prompt, top_k=10, query_embedding=query_embedding)
                if top_paths:
                    grounded = True
                    formatted = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(top_paths))
                    final_prompt = prompt + GROUNDING_TEMPLATE.format(
                        top_k=len(top_paths),
                        topic_paths=formatted
                    )
            except Exception as e:
                # If vector search fails (e.g. empty DB), fall back gracefully
                logger.info(f"[SmartIngest] Vector lookup failed, continuing without grounding: {e}")

        self.last_extraction_grounded = grounded

        raw_json = self.engine.generate(
            prompt=final_prompt,
            system_prompt=self.sys_prompt,
            json_schema=ChatExtraction.model_json_schema(),
            max_tokens=25000,
            temperature=0.7
        )
        if not raw_json:
            return None
        try:
            validated_data = ChatExtraction.model_validate_json(raw_json)
            return validated_data.model_dump()
        except Exception as e:
            logger.info(f"[SafeAI] Validation Failed: {e}")
            return None