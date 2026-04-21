import sys
import os
os.environ["HF_HOME"] = "E:/MindCache/hf_cache"

from retrieval.active_path import ActivePathRetrieval
from retrieval.structs import RetrievalConfig
# Tells Hugging Face to download and cache models on the roomy E: drive


def run_test_query(query):
    print(f"\n--- Searching for: '{query}' ---")
    config = RetrievalConfig()
    ap = ActivePathRetrieval(config)
    result = ap.retrieve(current_prompt=query)
    
    print(f"\n[FINAL CONTEXT]\n{result}")

if __name__ == "__main__":
    query = "Give me every detailed context about product comparator project." 
    if len(sys.argv) > 1:
        query = sys.argv[1]
    run_test_query(query)
