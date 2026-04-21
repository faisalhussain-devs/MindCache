import time
from Database.db_manager import DatabaseManager
from Database.nodes_summary import RecursiveSummarizer
from Database.reorganize_tree import reorganize_tree
from Database.embedder import run_embedding_job

COOLDOWN_SECONDS = 10

def _run_step(name, fn):
    """Wraps a job in try/except so one failure doesn't kill the pipeline."""
    print(f" RUNNING: {name}")
    try:
        fn()
    except Exception as e:
        print(f"  [ERROR] {name} failed: {e}")

def _prewarm_retrieval_cache():
    """
    Rebuild the tree cache and pre-warm the root leaf cache.
    Called as the final step after all data-modifying jobs complete.
    """
    from api_server import get_tree_cache
    from retrieval.root_cache import root_leaf_cache

    # Clear stale tree cache and rebuild
    get_tree_cache.cache_clear()
    tree = get_tree_cache()

    # Build the leaf cache for every root
    root_leaf_cache.build_all(tree)


def run_all_jobs(cooldown=COOLDOWN_SECONDS):
    """
    Runs all compute-heavy background jobs sequentially with cooldown gaps.
    
    Pipeline order:
      1. Tree Reorganization (LLM — restructures topology)
      2. Decision Analyzer  (lightweight — enriches decision context)
      3. Node Summaries and Description     (LLM-heavy — generates descriptions)
      4. Embedding Job       (GPU-heavy — generates description vectors)
      5. Cache Pre-warm      (CPU — rebuilds tree + leaf caches for retrieval)
    """
    db = DatabaseManager()
    
    jobs = [
        ("Memory Extractor", lambda: db.process_memory()),
        ("Tree Reorganization", lambda: reorganize_tree()), 
        ("Decision Analyzer", lambda: db.run_decision_state_analyzer()),
        ("Node Summaries", lambda: RecursiveSummarizer().run()),
        ("Embedding Cache", lambda: run_embedding_job()),
        ("Retrieval Cache Pre-warm", _prewarm_retrieval_cache),
    ]
    
    for i, (name, fn) in enumerate(jobs):
        _run_step(name, fn)
        if i < len(jobs) - 1:
            print(f"\n  Cooling down for {cooldown}s...")
            time.sleep(cooldown)
    
    print(" ALL BACKGROUND JOBS COMPLETE")


if __name__ == "__main__":
    import sys
    is_dry_run = "--apply" not in sys.argv
    cooldown = COOLDOWN_SECONDS
    
    for arg in sys.argv:
        if arg.startswith("--cooldown="):
            cooldown = int(arg.split("=")[1])
    
    if is_dry_run:
        print("NOTE: Tree reorganization in DRY-RUN mode. Pass '--apply' to alter DB.")
    print(f"Cooldown between jobs: {cooldown}s\n")
    run_all_jobs(cooldown=cooldown)
