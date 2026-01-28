from safe_ai import SafeAI
from schema import ChatExtraction
import json
import sys

# Extract the schema from your Pydantic model
schema = ChatExtraction.model_json_schema()
print(json.dumps(schema, indent=2))
sys.exit(0)

SYSTEM_PROMPT = f""" You are the "MindCache Extraction Engine." Your goal is to read a conversation (User Input + AI Response) and extract permanent information into a strict JSON format.

### 1. THE EXTRACTION LOGIC
You must process the input in two phases:
PHASE A: REASONING (The Filter)
- Break the input into atomic thoughts.
- Classify each thought as 'user', 'fact', 'epis' (episodic), or 'noise'.
- "Noise" (greetings, thanks, small talk) must be discarded from the final memory.

PHASE B: CONSOLIDATION (The Memory)
Populate the "memory" object based on your reasoning tags.

### 2. DEFINITIONS & RULES (Strict Adherence)

[USER] -> "Who they are & How they want me to behave"
- User preferences (e.g., "Don't use code blocks", "I prefer Python").
- Biographical details (e.g., "I live in Berlin", "I am a Data Scientist").
- Behavioral constraints (e.g., "Be concise", "Never apologize").

[FACT] -> "Project Knowledge, Lessons, & Universal Truths"
- **CRITICAL:** This now includes *Experience* and *Technical Constraints*.
- If the user says "I switched to SQLite because MongoDB was too heavy," the FACT is: "Constraint: SQLite is preferred over MongoDB for this project due to memory weight."
- Store: Project requirements, code snippets explained, solutions that worked, and specific technical decisions.

[EPIS] -> "The Timeline of Events"
- A log of actions taken in *this* specific turn.
- e.g., "User debugged the database schema," "User rejected the first draft," "AI provided the BLOB conversion script."
- Keep this concise. It is for tracking *what happened*, not *what is true*.

[TOPICS] -> "Indexing Tags"
- Extract 3-5 high-level keywords (e.g., "Python", "Database", "Debugging").
- Range from broad (Cluster) to specific (Entity).

### 3. Schema
 {json.dumps(schema, indent=2)}

### 4. FINAL INSTRUCTION
Your output must be VALID JSON matching the provided schema exactly. Do not include markdown formatting or explanations outside the JSON."""

class memory_extractor():
    def __init__(self, model_name="qwen3-fast"):
        self.engine = SafeAI(model_name=model_name)

    def memory_extract(self, prompt):
        raw_json = self.engine.generate(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT
        )
        if not raw_json:
            return None
        try:
            raw_json = raw_json.replace("<think>", "").replace("</think>", "")
            clean_json = self.engine.clean_json(raw_json)
            print(clean_json)
            validated_data = ChatExtraction.model_validate_json(clean_json)
            return validated_data.model_dump()
        except Exception as e:
            print(f"[SafeAI] Validation Failed: {e}")
            return None