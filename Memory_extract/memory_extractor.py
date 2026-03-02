from Memory_extract.safe_ai import SafeAI
from Memory_extract.schema import ChatExtraction
from Database.db_manager import DatabaseManager

SYSTEM_PROMPT = """You are the MindCache Extraction Engine. Your goal is to read a conversation (User Input + AI Response) and extract permanent information into a strict JSON format.

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
- Example: If root is ["Travel", "Florida"], branch might be ["Orlando", "Dining"].
- **IMPORTANT:** If existing topics are listed below, reuse those exact names when they match what you're extracting.

[USER] -> "Preferences & Profile"
- "Who they are", "What they like", and "Experiences they've had".
- e.g., "I plan to visit Bandung," "I prefer quiet hotels," "I have three dogs."

[FACT] -> "Entities, Facts & Specifics"
- **CRITICAL:** Store concrete information: names, places, processes, technical details, or specific recommendations mentioned in the chat.
- If the chat mentions: "The Sugar Factory at Icon Park has giant milkshakes."
- FACT: "Entity: The Sugar Factory is located at Icon Park and is known for giant milkshakes."
- This is the most important bucket for answering factual recall questions later.

[EPIS] -> "The Narrative & Context"
- A brief log of *what happened* or *what was discussed* in this specific turn.
- e.g., "AI explained refining processes at CITGO's Lake Charles Refinery," "User asked for dessert recommendations in Orlando."

[DECISION] -> "The Why"
- Explicitly store the reasoning behind choices or recommendations made during the chat.
- e.g., "Recommended The Sugar Factory because user specifically asked for unique, large desserts."

### 3. FINAL INSTRUCTION
Your output must be VALID JSON matching the ChatExtraction schema.
- Do not include explanations outside the JSON object.
"""

GROUNDING_TEMPLATE = """

### EXISTING TOPICS (reuse these exact names when applicable)
{topic_tree}
"""

class Memory_Extractor():
    def __init__(self, sys_prompt=SYSTEM_PROMPT):
        self.engine = SafeAI()
        self.sys_prompt = sys_prompt
        self._db = DatabaseManager()

    def memory_extract(self, prompt=""):
        # Inject existing topic tree into prompt for grounding
        final_prompt = prompt
        topic_hints = self._db.get_topic_tree_hints()
        if topic_hints:
            final_prompt = prompt + GROUNDING_TEMPLATE.format(topic_tree=topic_hints)

        raw_json = self.engine.generate(
            prompt=final_prompt,
            system_prompt=self.sys_prompt,
            json_schema=ChatExtraction.model_json_schema()
        )
        if not raw_json:
            return None
        try:
            validated_data = ChatExtraction.model_validate_json(raw_json)
            return validated_data.model_dump()
        except Exception as e:
            print(f"[SafeAI] Validation Failed: {e}")
            return None