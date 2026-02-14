import os
from Memory_extract.memory_extractor import Memory_Extractor
from Memory_extract.input_denoiser import InputDenoiser
from Database.db_manager import DatabaseManager
from Database.db_setup import ProcessingJob, TriadBlock, Topic, DecisionMemory
from Database.nodes_summary import RecursiveSummarizer
from Database.embedder import run_embedding_job

recursive_summarizer = RecursiveSummarizer()
mem_ext = Memory_Extractor()
inp_denoiser = InputDenoiser()
db_manager = DatabaseManager()

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


def run_test(inputs):
    session = db_manager.Session()
    
    try:
        session.query(ProcessingJob).delete()
        session.query(TriadBlock).delete()
        session.query(Topic).delete()
        session.commit()
        print("Database cleared.\n")

        print("--- 2. SIMULATING USER INPUT (Buffer) ---")
        for prompt, response, next_prompt in inputs:
            db_manager.add_to_queue(prompt, response, next_prompt)
        
        pending_count = session.query(ProcessingJob).count()
        print(f"Queue Status: {pending_count} jobs pending.\n")

    except Exception as e:
        session.rollback()
        print(f"Initialization Error: {e}")
        return
    finally:
        session.close()

    print("--- 3. STARTING WORKER LOOP (Processing) ---")
    
    while True:
        job = db_manager.get_pending_job()
        
        if not job:
            print("No more jobs in queue. Worker going to sleep.")
            break

        job_id, prompt, response, next_prompt = job["id"], job["raw_prompt"], job["raw_response"], job["raw_next_prompt"]
        full_text = f"<user> {prompt} <ChatGPT> {response} <user> {next_prompt}"
        
        compressed_input = inp_denoiser.compress(full_text)
        
        print(f"\n[Worker] Processing Job #{job_id}...")
    
        extracted_data = mem_ext.memory_extract(compressed_input)     

        db_manager.save_extracted_memory(
            job_id,
            compressed_input, 
            extracted_data
        )

    print("\n--- 4. VERIFYING VAULT RESULTS ---")
    
    try:
        # Check Buffer (Should be 0)
        buffer_count = session.query(ProcessingJob).count()
        print(f"Buffer Remaining: {buffer_count} (Should be 0)")

        # Check Parents (TriadBlocks)
        parent_count = session.query(TriadBlock).count()
        print(f"Conversation Turns: {parent_count}")

        # Check Children (Memories)
        memories = session.query(Memory).all()
        print(f"\nSaved Memory Atoms ({len(memories)} total):")
        
        for mem in memories:
            # We can access the related topic name easily via the relationship
            topic_name = mem.topic.name if mem.topic else "Unknown"
            print(f" - [{topic_name}] [{mem.type.upper()}]: {mem.content[:50]}...")

    finally:
        session.close()
    recursive_summarizer.run()
    run_embedding_job()
    

if __name__ == "__main__":
    data = raw_data()
    inputs = []
    next_prompt = "Good it worked. Thankyou"  # telling about the previous thing it worked or not 
    for input in data:
        prompt, response = input.split("<ChatGPT>")
        inputs.append((prompt, response, next_prompt))
    run_test(inputs)