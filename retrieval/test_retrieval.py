import sys
from retrieval.active_path import ActivePathRetrieval
from retrieval.structs import RetrievalConfig

def run_test_query(query):
    print(f"\n--- Searching for: '{query}' ---")
    config = RetrievalConfig()
    ap = ActivePathRetrieval(config)
    result = ap.retrieve(current_prompt=query)
    
    print("\n[ACTIVE PATH TRACE]")
    for log in result.debug_log:
        print(f"  {log}")
        
    print(f"\n[FINAL CONTEXT]\n{result.context_str}")
    
    print("\n[SOURCES]")
    for src in result.sources:
        print(f"  - {src['name']} ({src['type']}) Score: {src['score']:.3f}")

if __name__ == "__main__":
    query = "What about the visa internship. what are its eligibility criteria. will i get the internship ?" 
    if len(sys.argv) > 1:
        query = sys.argv[1]
    run_test_query(query)
