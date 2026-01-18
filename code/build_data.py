import json
import os

def merge_data():
    # CONFIG
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Go UP one level to the project root (Project/)
    project_root = os.path.dirname(script_dir)

    # 3. Build the safe paths to the data folder
    raw_input_file = os.path.join(project_root, "data", "raw_chats.txt")
    ai_output_file = os.path.join(project_root, "data", "raw_chats_labels.json")
    final_dataset_file = os.path.join(project_root, "data", "golden_dataset.json")

    DELIMITER = "===END==="

    # 1. READ RAW INPUTS (Text Splitting)
    try:
        with open(raw_input_file, "r", encoding="utf-8") as f:
            full_text = f.read()
            
        # Split by the delimiter and strip whitespace
        # Filter out empty strings (in case of trailing delimiter)
        inputs = [
            chat.strip() 
            for chat in full_text.split(DELIMITER) 
            if chat.strip()
        ]
        
    except FileNotFoundError:
        print(f" Error: '{raw_input_file}' not found.")
        return

    # 2. READ AI OUTPUTS (JSON Parsing)
    try:
        with open(ai_output_file, "r", encoding="utf-8") as f:
            outputs = json.load(f)
            
        if not isinstance(outputs, list):
            print(f" Error: '{ai_output_file}' must be a JSON LIST [...]")
            return
            
    except json.JSONDecodeError as e:
        print(f" Error parsing '{ai_output_file}'. Check commas/brackets.\n{e}")
        return

    # 3. VERIFY COUNTS
    print(f" Stats: Found {len(inputs)} Chats and {len(outputs)} JSON Objects.")
    
    if len(inputs) != len(outputs):
        print("  MISMATCH WARNING!")
        print("The number of chats does not match the number of JSON outputs.")
        print("Please check if you missed a delimiter '===END===' or an AI response.")
        # We will stop to prevent data misalignment
        return

    # 4. MERGE
    new_entries = []
    for i in range(len(inputs)):
        entry = {
            "input": inputs[i],
            "output": outputs[i]
        }
        new_entries.append(entry)

    # 5. SAVE OR APPEND
    existing_data = []
    if os.path.exists(final_dataset_file):
        with open(final_dataset_file, "r", encoding="utf-8") as f:
            try:
                existing_data = json.load(f)
            except:
                existing_data = []

    final_data = existing_data + new_entries

    with open(final_dataset_file, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)

    print(f" Success! Merged {len(new_entries)} pairs into '{final_dataset_file}'.")

if __name__ == "__main__":
    merge_data()