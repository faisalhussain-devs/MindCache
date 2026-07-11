import time
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mindcache.Database.db_manager import DatabaseManager
from mindcache.Database.nodes_summary import RecursiveSummarizer
from mindcache.Database.reorganize_tree import reorganize_tree
from mindcache.Database.embedder import run_embedding_job
from eval.ingest_api import adapt_beam, create_jobs, run_jobs, show_status, retry_failed
import logging
logger = logging.getLogger(__name__)


COOLDOWN_SECONDS = 10

def auto_ingestion_beam():
    i = 3
    input_file = "BEAM/0000.parquet"
    output_file = f"BEAM/eval_data_beam-1M-{i}.json"
    adapt_beam(parquet_path=input_file, output_path=output_file, conv_index=i)
    create_jobs(input_path=output_file, reset=True)
    status = show_status()
    if status['failed'] > 0:
        retry_failed()

    while status['pending'] > 0:
        run_jobs()
        status = show_status()
        if status['failed'] > 0:
            retry_failed()
        status = show_status()

def _run_step(name, fn):
    """Wraps a job in try/except so one failure doesn't kill the pipeline."""
    logger.info(f" RUNNING: {name}")
    try:
        fn()
    except Exception as e:
        logger.error(f"  [ERROR] {name} failed: {e}")
    finally:
        import gc
        gc.collect()

def _prewarm_retrieval_cache():
    """
    Rebuild the tree cache and pre-warm the collapsed tree cache.
    Called as the final step after all data-modifying jobs complete.
    """
    from mindcache.retrieval.root_cache import get_tree_cache, CollapsedTreeCache

    get_tree_cache.cache_clear()
    tree = get_tree_cache()

    CollapsedTreeCache().build_all(tree)


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
        ("Memory Extractor", lambda: auto_ingestion_beam()),
        #("Tree Reorganization", lambda: reorganize_tree()), 
        ("Decision Analyzer", lambda: db.run_decision_state_analyzer()),
        #("Node Summaries", lambda: RecursiveSummarizer().run()),
        ("Embedding Cache", lambda: run_embedding_job()),
        ("Pre-warm Retrieval Cache", lambda: _prewarm_retrieval_cache()),
    ]
    
    for i, (name, fn) in enumerate(jobs):
        _run_step(name, fn)
        import gc
        gc.collect()
        if i < len(jobs) - 1:
            logger.info(f"\n  Cooling down for {cooldown}s...")
            time.sleep(cooldown)
    
    logger.info(" ALL BACKGROUND JOBS COMPLETE")


if __name__ == "__main__":
    import sys
    is_dry_run = "--apply" not in sys.argv
    cooldown = COOLDOWN_SECONDS
    
    for arg in sys.argv:
        if arg.startswith("--cooldown="):
            cooldown = int(arg.split("=")[1])
    
    logger.info(f"Cooldown between jobs: {cooldown}s\n")
    run_all_jobs(cooldown=cooldown)