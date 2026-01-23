from Memory_extract.safe_ai import SafeAI
from Memory_extract.schema import ChatExtraction

SYSTEM_PROMPT = """ You are the "MindCache Extraction Engine." Your goal is to read a conversation (User Input + AI Response) and extract permanent information into a strict JSON format.

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

### 3. EXAMPLE INPUT & OUTPUT

INPUT: 
"I tried using JSON strings for the vectors, but the retrieval was too slow (500ms). I switched to Binary BLOBs and it dropped to 10ms. Let's stick to BLOBs from now on. Also, stop calling me 'Sir'."

OUTPUT JSON:
{
  "reasoning": [
    { "txt": "User tested JSON storage; found high latency (500ms).", "tag": "epis" },
    { "txt": "User switched to Binary BLOBs; latency dropped to 10ms.", "tag": "fact" },
    { "txt": "Directive: Use BLOBs for all future vector storage.", "tag": "fact" },
    { "txt": "User preference: Do not use honorific 'Sir'.", "tag": "user" }
  ],
  "memory": {
    "topics": ["Optimization", "Database", "BLOB", "Latency", "Vectors"],
    "user": [
      "Preference: Do not refer to the user as 'Sir'."
    ],
    "fact": [
      "Technical Insight: Storing vectors as JSON strings causes high latency (~500ms).",
      "Best Practice: Use Binary BLOBs for vector storage to ensure low latency (~10ms).",
      "Project Constraint: All future vector implementations must use BLOB storage."
    ],
    "epis": [
      "User optimized database performance by migrating from JSON strings to Binary BLOBs."
    ]
  }
}

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

        if raw_json:
            raw_json = raw_json.replace("<think>", "").replace("</think>", "")
            clean_json = self.engine.clean_json(raw_json)
            print(clean_json)
            validated_data = ChatExtraction.model_validate_json(clean_json)
            return validated_data.model_dump()
        