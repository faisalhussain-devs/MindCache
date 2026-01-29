import os
import json
import numpy as np
from Memory_extract.memory_extractor import Memory_Extractor
from Memory_extract.input_denoiser import InputDenoiser
from Database.database import DatabaseManager

mem_ext = Memory_Extractor()
inp_denoiser = InputDenoiser()

def raw_data():
    project_root = os.path.dirname(os.path.abspath(__file__))

    raw_input_file = os.path.join(project_root, "data", "raw_chats.txt")

    DELIMITER = "===END==="

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
    return inputs
    

def mock_get_embedding(text):
    """Simulates generating a 384-dimensional vector."""
    return np.random.rand(384).astype(np.float32)


def run_test(inputs):
    print("--- 1. INITIALIZING DATABASE ---")
    db = DatabaseManager()

    db.cursor.execute("DELETE FROM processing_queue")
    db.cursor.execute("DELETE FROM conversation_turns")
    db.cursor.execute("DELETE FROM memory_atoms")
    db.conn.commit()
    print("Database cleared.\n")

    print("--- 2. SIMULATING USER INPUT (Buffer) ---")

    for prompt, response, next_prompt in inputs:
        db.add_to_queue(prompt, response, next_prompt)
    
    db.cursor.execute("SELECT count(*) FROM processing_queue")
    print(f"Queue Status: {db.cursor.fetchone()[0]} jobs pending.\n")

    print("--- 3. STARTING WORKER LOOP (Processing) ---")
    
    while True:
        # A. Fetch Job
        job = db.get_pending_job()
        if not job:
            print("No more jobs in queue. Worker going to sleep.")
            break
            
        job_id, prompt, response, next_prompt = job

        # B. Simulate AI Extraction
        input = prompt+" <ChatGPT> "+response+ "<user>"+ next_prompt
        compressed_input = inp_denoiser.compress(input)
        print(f"\n[Worker] Processing Job #{job_id}: '{compressed_input}'")
        extracted_data = mem_ext.memory_extract(compressed_input)
        print(f"   -> Extracted JSON: {json.dumps(extracted_data['memory'].get('topics', []))}")

        # C. Simulate Embedding Generation
        topic_vec = mock_get_embedding("sample topic string")
        
        content_vec_map = {"user": [], "fact": [], "epis": []}
        mem_block = extracted_data.get("memory", {})
        
        for m_type in ["user", "fact", "epis"]:
            items = mem_block.get(m_type, [])
            for item in items:
                # Generate one fake vector per item
                content_vec_map[m_type].append(mock_get_embedding(item))

        # D. Save to Vault
        db.save_extracted_memory(job_id, extracted_data, topic_vec, content_vec_map)

    print("\n--- 4. VERIFYING VAULT RESULTS ---")
    
    # Check Buffer (Should be 0)
    db.cursor.execute("SELECT count(*) FROM processing_queue")
    buffer_count = db.cursor.fetchone()[0]
    print(f"Buffer Remaining: {buffer_count} (Should be 0)")

    # Check Parents (Should be 2, because 'Hello' was noise)
    db.cursor.execute("SELECT count(*) FROM conversation_turns")
    parent_count = db.cursor.fetchone()[0]
    print(f"Conversation Turns: {parent_count} (Should be 2)")

    # Check Children (Atoms)
    db.cursor.execute("SELECT memory_type, content_text FROM memory_atoms")
    rows = db.cursor.fetchall()
    print(f"\nSaved Memory Atoms ({len(rows)} total):")
    for r in rows:
        print(f" - [{r[0].upper()}] {r[1]}")

    if buffer_count == 0 and parent_count == 2:
        print("\n TEST PASSED: Pipeline is working correctly!")
    else:
        print("\n TEST FAILED: Counts do not match expected values.")

if __name__ == "__main__":
    data = raw_data()
    inputs = []
    next_prompt = "Good it worked. Thankyou"  # telling about the previous thing it worked or not 
    for input in data:
        prompt, response = input.split("<ChatGPT>")
        inputs.append((prompt, response, next_prompt))
    run_test(inputs)