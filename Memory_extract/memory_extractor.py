from Memory_extract.safe_ai import SafeAI
from Memory_extract.schema import ChatExtraction

# Extract the schema from your Pydantic model
schema_json = ChatExtraction.model_json_schema()
SYSTEM_PROMPT = f"""You are the MindCache Extraction Engine. Your goal is to read a conversation (User Input + AI Response) and extract permanent information into a strict JSON format.

### 1. THE EXTRACTION LOGIC
You must populate the JSON fields following this strict logic:

**FIELD 1: "reasoning" (Phase A)**
- Think step-by-step. Analyze the input to decide what is worth saving.
- Classify thoughts as 'user', 'fact', 'epis', or 'noise'.
- Explicitly state *why* you are choosing specific topics.

**FIELD 2: "topics_root" (Global Context)**
- The high-level Project or Domain that applies to *all* memories in this turn.
- Example: ["MindCache", "Backend"] or ["Personal", "Travel"].

**FIELD 3: "memory" (The Data Buckets)**
- A list of objects. You can create multiple buckets if the user talks about different sub-topics (e.g., one bucket for "Database" and another for "API").
- Inside each bucket, populate 'topics_branch', 'user', 'fact', and 'epis'.

### 2. DEFINITIONS (Strict Adherence)

[topics_branch] -> "Sub-Folder Routing"
- The specific sub-path for this memory bucket.
- Example: If root is ["MindCache"], branch might be ["Database", "Migrations"].

[USER] -> "Preferences & Bio"
- "Who they are" & "How they want me to behave".
- e.g., "I prefer Python," "Don't use code blocks," "I live in Berlin."

[FACT] -> "Universal Truths & Constraints"
- **CRITICAL:** Store technical constraints, code logic, and project decisions here.
- If user says: "I switched to SQLite because MongoDB was heavy,"
- FACT: "Constraint: SQLite is preferred over MongoDB due to memory weight."

[EPIS] -> "The Narrative"
- A brief log of *actions* taken in this specific turn.
- e.g., "User provided the initial schema," "AI debugged the connection error."

[DECISION] -> "The Why"
- Explicitly store the reasoning behind choices.
- e.g., "Chose cosine similarity over euclidean distance for better text matching."

### 3. SCHEMA
{schema_json}

### 4. FINAL INSTRUCTION
Your output must be VALID JSON matching the provided schema exactly. 
- Do not include markdown formatting (```json ... ```). 
- Do not include explanations outside the JSON object.
"""

class Memory_Extractor():
    def __init__(self, sys_prompt=SYSTEM_PROMPT):
        self.engine = SafeAI()
        self.sys_prompt = sys_prompt

    def memory_extract(self, prompt):
        raw_json = self.engine.generate(
            prompt=prompt,
            system_prompt=self.sys_prompt
        )
        if not raw_json:
            return None
        try:
            validated_data = ChatExtraction.model_validate_json(raw_json)
            return validated_data.model_dump()
        except Exception as e:
            print(f"[SafeAI] Validation Failed: {e}")
            return None