import sys
import os
os.environ["HF_HOME"] = "E:/MindCache/hf_cache"

from retrieval.active_path import ActivePathRetrieval
import logging
logger = logging.getLogger(__name__)
# Tells Hugging Face to download and cache models on the roomy E: drive


def run_test_query(query):
    logger.info(f"\n--- Searching for: '{query}' ---")
    ap = ActivePathRetrieval()
    result = ap.retrieve(current_prompt=query)
    
    logger.info(f"\n[FINAL CONTEXT]\n{result.context}")

if __name__ == "__main__":
    query = "How many days passed between the argument we had about weekend plans and the communication workshop where April mentioned feeling neglected?"
    if len(sys.argv) > 1:
        query = sys.argv[1]
    run_test_query(query)