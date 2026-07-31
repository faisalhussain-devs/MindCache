import os
from mindcache.Memory_extract.memory_extractor import Memory_Extractor
from mindcache.Memory_extract.input_denoiser import InputDenoiser
from mindcache.Database.db_manager import DatabaseManager
from mindcache.Database.db_setup import ProcessingJob, TriadBlock, Topic, DecisionMemory, EpisodicMemory, KnowledgeMemory, UserMemory
from mindcache.Database.nodes_summary import RecursiveSummarizer
from mindcache.Database.embedder import run_embedding_job
import logging
logger = logging.getLogger(__name__)

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
        logger.error(f" Error: '{raw_input_file}' not found.")
    return inputs


def run_test(inputs):
    session = db_manager.Session()
    
    try:
        session.query(ProcessingJob).delete()
        session.query(TriadBlock).delete()
        session.query(DecisionMemory).delete()
        session.query(EpisodicMemory).delete()
        session.query(KnowledgeMemory).delete()
        session.query(UserMemory).delete()
        session.query(Topic).delete()
        session.commit()
        logger.info("Database cleared.\n")

        logger.info("--- 2. SIMULATING USER INPUT (Buffer) ---")
        for prompt, response, next_prompt in inputs:
            full_prompt = f"<user> {prompt} <ChatGPT> {response} <user> {next_prompt}"
            db_manager.add_to_queue(full_prompt)
        
        pending_count = session.query(ProcessingJob).count()
        logger.info(f"Queue Status: {pending_count} jobs pending.\n")

    except Exception as e:
        session.rollback()
        logger.error(f"Initialization Error: {e}")
        return
    finally:
        session.close()

    logger.info("--- 3. STARTING WORKER LOOP (Processing) ---")
    
    while True:
        job = db_manager.get_pending_job()
        
        if not job:
            logger.info("No more jobs in queue. Worker going to sleep.")
            break

        job_id, prompt = job["id"], job["raw_prompt"]
        compressed_input = inp_denoiser.compress(prompt)
        
        logger.info(f"\n[Worker] Processing Job #{job_id}...")
    
        extracted_data = mem_ext.memory_extract(compressed_input)     

        db_manager.save_extracted_memory(
            job_id,
            compressed_input, 
            extracted_data
        )

    logger.info("\n--- 4. VERIFYING VAULT RESULTS ---")
    
    try:
        # Check Buffer (Should be 0)
        buffer_count = session.query(ProcessingJob).count()
        logger.info(f"Buffer Remaining: {buffer_count} (Should be 0)")

        # Check Parents (TriadBlocks)
        parent_count = session.query(TriadBlock).count()
        logger.info(f"Conversation Turns: {parent_count}")

        models = [
            (EpisodicMemory, "EPISODIC"),
            (UserMemory, "USER"),
            (KnowledgeMemory, "KNOWLEDGE"),
            (DecisionMemory, "DECISION")
        ]
        
        total_memories = 0
        logger.info(f"\nSaved Memory Atoms:")
        
        for Model, label in models:
            mems = session.query(Model).all()
            total_memories += len(mems)
            for mem in mems:
                topic_name = mem.topic.name if mem.topic else "Unknown"
                content = mem.content[:50] if mem.content else "(No Content)"
                logger.info(f" - [{topic_name}] [{label}]: {content}...")

        logger.info(f"\nTotal Memories: {total_memories}")

    finally:
        session.close()
    db_manager.run_decision_state_analyzer()
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