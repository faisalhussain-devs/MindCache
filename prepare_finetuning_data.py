import json
import os
import argparse

def extract_conversations(filepath, output_path):
    print(f"Loading {filepath} (This might take a moment...)")
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    all_threads = []

    for conv in data:
        mapping = conv.get("mapping", {})
        if not mapping:
            continue
            
        # Find leaf nodes (nodes with no children)
        # These represent the end of a conversation branch
        leaf_nodes = [
            node_id for node_id, node_data in mapping.items()
            if not node_data.get("children")
        ]
        
        # Helper to get message content
        def get_message_data(node_id):
            node = mapping.get(node_id, {})
            msg = node.get("message")
            if not msg:
                return None
            author = msg.get("author", {}).get("role")
            content_dict = msg.get("content", {})
            parts = content_dict.get("parts", [])
            
            create_time = msg.get("create_time")
            human_time = None
            if create_time:
                import datetime
                human_time = datetime.datetime.fromtimestamp(create_time).strftime('%Y-%m-%d %H:%M:%S')
            
            # parts can sometimes contain dicts (like for images), ensure we only get text
            text_parts = [p for p in parts if isinstance(p, str)]
            text = "".join(text_parts).strip()
            
            if not text or not author:
                return None
            return {
                "role": author, 
                "content": text, 
                "timestamp": create_time,
                "datetime": human_time
            }

        # Reconstruct path backwards from each leaf
        for leaf in leaf_nodes:
            current_id = leaf
            thread = []
            
            # Walk backwards up the tree
            while current_id:
                msg_data = get_message_data(current_id)
                if msg_data:
                    thread.append(msg_data)
                
                # Move to parent
                current_node = mapping.get(current_id, {})
                current_id = current_node.get("parent")
            
            # Reverse to make it chronological
            thread.reverse()
            
            # Only keep threads that actually have meaningful back-and-forth
            if len(thread) > 1:
                all_threads.append(thread)

    # Sort all conversations globally by the timestamp of their first message
    all_threads.sort(key=lambda thread: thread[0].get("timestamp") or 0.0)

    print(f"Extracted {len(all_threads)} distinct conversation branches.")

    # Restructure into flat pairs: {prompt, response, time}
    # Many fine-tuning frameworks easily accept a massive flat list of prompt-response pairs.
    finetuning_pairs = []
    
    for thread in all_threads:
        current_prompt = None
        current_time = None
        
        # We group each conversation thread logically as lists of pairwise dicts
        conversation_pairs = []
        for msg in thread:
            if msg["role"] == "user":
                current_prompt = msg["content"]
                current_time = msg["datetime"] or msg["timestamp"]
            elif msg["role"] == "assistant" and current_prompt:
                conversation_pairs.append({
                    "prompt": current_prompt,
                    "response": msg["content"],
                    "time": current_time
                })
                # Reset for the next turn
                current_prompt = None
                
        if conversation_pairs:
            finetuning_pairs.append(conversation_pairs)

    with open(output_path, 'w', encoding='utf-8') as out_f:
        json.dump(finetuning_pairs, out_f, indent=2, ensure_ascii=False)
        
    print(f"Successfully saved flattened conversations to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract linear conversation threads from ChatGPT export.")
    parser.add_argument("--input", default=r"e:\MindCache\LLM_CHATS\GPT\27daf61bbfa0cb23b81db1fe3ed4c7a3\conversations.json")
    parser.add_argument("--output", default=r"e:\MindCache\finetuning_dataset.json")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"Error: Could not find input file at {args.input}")
    else:
        extract_conversations(args.input, args.output)
