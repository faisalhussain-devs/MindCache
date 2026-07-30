import json
import os
import sys

def convert_results_to_prompts(input_path="E:\MindCache\results\beam_conv28_results.json", output_path="results/beam_conv28_prompts.json"):
    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' not found.")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    prompts_list = []

    for idx, item in enumerate(data, start=1):
        q_id = item.get("question_id", f"q_{idx}")
        question = item.get("question", "")
        system_prompt = item.get("system_hint", "")
        retrieved_context = item.get("retrieved_context", "")
        expected = item.get("expected", "")
        rubric = item.get("rubric", [])

        # 1. Formatted User Content (Context + Question)
        user_content = f"RETRIEVED CONTEXT:\n{retrieved_context}\n\nQUESTION:\n{question}"

        # 2. Combined Single-String Prompt (for raw text LLMs / Completion API)
        full_text_prompt = f"SYSTEM INSTRUCTION:\n{system_prompt}\n\nRETRIEVED CONTEXT:\n{retrieved_context}\n\nQUESTION:\n{question}\n\nANSWER:"

        # 3. Standard Chat API Messages Array (OpenAI / Gemini / Anthropic)
        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        prompt_entry = {
            "index": idx,
            "question_id": q_id,
            "full_text_prompt": full_text_prompt,
            "generated_answer": ""
        }

        prompts_list.append(prompt_entry)

    # Save to output JSON file
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(prompts_list, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated {len(prompts_list)} prompt objects and saved to: {output_path}")

if __name__ == "__main__":
    inp = sys.argv[1] if len(sys.argv) > 1 else "results/beam_conv28_results.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "results/beam_conv28_prompts.json"
    convert_results_to_prompts(inp, out)
