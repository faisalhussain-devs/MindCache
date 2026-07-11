"""
MindCache Quickstart Example
----------------------------
Demonstrates: initialize -> add conversations -> process_queue -> search
"""
import os
import sys

# Add project root to sys.path so we can import mindcache locally
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindcache import MindCache

def main():
    # 1. Initialize SDK
    # We will use a demo database file for this run
    db_path = "quickstart_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    print("Initializing MindCache Client...")
    # NOTE: Set your GEMINI_API_KEY environment variable to test extraction.
    # Otherwise, it will fail gracefully or fall back to LiteLLM if configured.
    mc = MindCache(
        db_path=db_path,
        provider="gemini",
        model_name="gemini-2.5-flash",
    )

    # 2. Add conversation to queue (non-blocking, completed in milliseconds)
    print("\nAdding messages to queue...")
    job_id = mc.add([
        {"role": "user", "content": "I prefer Python and FastAPI for backend development, and Postgres for DB."},
        {"role": "assistant", "content": "Got it! I will remember your preference for Python, FastAPI, and Postgres."}
    ], user_id="alice")
    print(f"Queued successfully! Ingestion Job ID: {job_id}")

    # 3. Process the queue (runs extraction + decision analyzer + cache pre-warm)
    print("\nProcessing queue (running LLM extraction)...")
    try:
        results = mc.process_queue(user_id="alice")
        print(f"Queue processed. Results: {results}")
    except Exception as e:
        print(f"Ingestion failed (did you set GEMINI_API_KEY?): {e}")
        return

    # 4. Search retrieved context
    print("\nSearching Alice's memories...")
    context = mc.search("What is Alice's preferred database?", user_id="alice")
    print("\nRetrieved Context:")
    print(context)

    # Clean up demo database
    from mindcache.Database.db_setup import engine
    engine.dispose()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

if __name__ == "__main__":
    main()
