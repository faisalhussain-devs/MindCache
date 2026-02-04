import json
from retrieval.structs import RetrievalConfig, RefinedSelection
from Memory_extract.safe_ai import SafeAI

SYSTEM_TEMPLATE = """You are the Retrieval Refinement Engine.
Your goal is to select the exact pieces of information needed to answer the user's query from the providing "Database View".

### 1. INPUTS
- User Query: The immediate question and the previous inputs to the system.
- Database View: A list of potentially relevant Topics and Memories found by vector search.

### 2. YOUR JOB
- Analyze the User Query for the intent of the user taking some context from past inputs if provided
- Look at the Database View.
- Select ONLY the items that are necessary or beneficial to answer the query.
- Use the IDs provided in the view.

### 3. OUTPUT FORMAT
You must output VALID JSON matching this schema:
{schema_json}

Do not output markdown or explanations outside the JSON.
"""

USER_TEMPLATE = """User Query: {query}

Database View:
{candidates}

Select the relevant items."""


class AgenticRefiner:
    def __init__(self, config: RetrievalConfig):
        self.config = config
        self.ai = SafeAI(model_name=config.model_name)
        
    def refine(self, query: str, candidates: list[dict]) -> dict:
        """
        Uses LLM to select the most relevant items from the candidates list.
        """
        if not candidates:
            return {"reasoning": "No candidates found.", "selected_items": []}

        schema = RefinedSelection.model_json_schema()
        sys_prompt = SYSTEM_TEMPLATE.format(schema_json=json.dumps(schema, indent=2))
        usr_prompt = USER_TEMPLATE.format(query=query, candidates=candidates)

        raw_response = self.ai.generate(usr_prompt, system_prompt=sys_prompt)
        if not raw_response:
            return {"reasoning": "Generation failed", "selected_items": []}
        print("RAW_RESPONSE AGENT REFINER", raw_response)
            
        try:
            raw_response = raw_response.replace("<think>", "").replace("</think>", "")
            clean_json = self.ai.clean_json(raw_response)
            selection = RefinedSelection.model_validate_json(clean_json)
            return selection.model_dump()
        except Exception as e:
            print(f"[AgenticRefiner] Validation Error: {e}")
            return {"reasoning": f"Validation Error: {str(e)}", "selected_items": []}
