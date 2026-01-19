from safe_ai import SafeAI
from Memory_extract.schema import ChatExtraction

engine = SafeAI(model_name="qwen3-fast")

def memory_extract():
    raw_json = engine.generate(
        prompt="User input here...",
        system_prompt="You are an extractor..."
    )

    if raw_json:
        clean_json = engine.clean_json(raw_json)
        validated_data = ChatExtraction.model_validate_json(clean_json)
        return validated_data.model_dump()

output = memory_extract()