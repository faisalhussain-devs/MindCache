import json
from retrieval.structs import RetrievalConfig, RefinedSelection
from Memory_extract.safe_ai import SafeAI

system_prompt = f"""You are the Retrieval Refinement Engine.
Your goal is to select the exact pieces of information needed to answer the user's query from the providing "Database View".

### 1. INPUTS
- User Query: The immediate question.
- Database View: A list of potentially relevant Topics and Memories found by vector search.

### 2. YOUR JOB
- Analyze the User Query.
- Look at the Database View.
- Select ONLY the items that are strictly necessary to answer the query.
- Use the IDs provided in the view.

### 3. OUTPUT FORMAT
You must output VALID JSON matching this schema:
{json.dumps(schema, indent=2)}

Do not output markdown or explanations outside the JSON.
"""

        user_prompt = f"""User Query: "{query}"

Database View:
{candidate_str}

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

        candidate_str = ""
        for item in candidates:
            candidate_str += f"- ID: {item.get('id')} | Type: {item.get('type')} | Name: {item.get('name')}\n"

        schema = RefinedSelection.model_json_schema()
        
        # 2. Call SafeAI
        raw_response = self.ai.generate(user_prompt, system_prompt=system_prompt)
        
        if not raw_response:
            return {"reasoning": "Generation failed", "selected_items": []}
            
        # 3. Parse & Validate
        try:
            clean_json = self.ai.clean_json(raw_response)
            # Validate with Pydantic
            selection = RefinedSelection.model_validate_json(clean_json)
            return selection.model_dump()
        except Exception as e:
            print(f"[AgenticRefiner] Validation Error: {e}")
            # Fallback: Just return empty or log error
            return {"reasoning": f"Validation Error: {str(e)}", "selected_items": []}
