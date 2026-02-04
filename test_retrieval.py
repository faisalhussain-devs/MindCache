import sys
from retrieval.active_path import ActivePathRetrieval
from retrieval.structs import RetrievalConfig

def run_test_query(query):
    print(f"\n--- Searching for: '{query}' ---")
    config = RetrievalConfig()
    ap = ActivePathRetrieval(config)
    result = ap.retrieve(current_prompt=query)
    
    print(f"\n[FINAL CONTEXT]\n{result}")

if __name__ == "__main__":
    query = "What about the visa internship. what are its eligibility criteria. will i get the internship ?" 
    if len(sys.argv) > 1:
        query = sys.argv[1]
    run_test_query(query)
