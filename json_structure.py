import json
import sys

def get_schema(data):
    if isinstance(data, dict):
        return {k: get_schema(v) for k, v in data.items()}
    elif isinstance(data, list):
        if len(data) == 0:
            return "list[Empty]"
        # To avoid massive output, just look at the first few distinct structures
        # Usually looking at the first element is enough for schema
        first_type = get_schema(data[0])
        return [first_type]
    elif isinstance(data, str):
        return "str"
    elif isinstance(data, int):
        return "int"
    elif isinstance(data, float):
        return "float"
    elif isinstance(data, bool):
        return "bool"
    elif data is None:
        return "null"
    else:
        return type(data).__name__

def analyze_json(file_path):
    print(f"Analyzing {file_path}...")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        schema = get_schema(data)
        
        print("\n=== JSON Structure Schema ===")
        print(json.dumps(schema, indent=2))
        
        if isinstance(data, list):
            print(f"\nTotal Top-Level Elements: {len(data)}")
            
    except Exception as e:
        print(f"Error parsing JSON: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python json_structure.py <path_to_json_file>")
    else:
        analyze_json(sys.argv[1])
