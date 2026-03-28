import time
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine
from Database.db_manager import DatabaseManager
from Database.nodes_summary import RecursiveSummarizer
from Database.reorganize_tree import reorganize_tree, repair_cycles
from embedder import run_embedding_job

COOLDOWN_SECONDS = 100

def _run_step(name, fn):
    """Wraps a job in try/except so one failure doesn't kill the pipeline."""
    print(f"\n{'='*50}")
    print(f" RUNNING: {name}")
    print(f"{'='*50}")
    try:
        fn()
    except Exception as e:
        print(f"  [ERROR] {name} failed: {e}")

def run_all_jobs(cooldown=COOLDOWN_SECONDS, dry_run=True):
    """
    Runs all compute-heavy background jobs sequentially with cooldown gaps.
    
    Pipeline order:
      1. Decision Analyzer  (lightweight — enriches decision context)
      2. Node Summaries     (LLM-heavy — generates descriptions)
      3. Tree Reorganization (LLM + embedder — restructures topology)
      4. Cycle Repair        (lightweight — safety sweep post-reorganization)
      5. Embedding Job       (GPU-heavy — generates topic vectors)
    """
    db = DatabaseManager()
    
    jobs = [
        ("Decision Analyzer", lambda: db.run_decision_state_analyzer()),
        ("Node Summaries", lambda: RecursiveSummarizer().run()),
        ("Tree Reorganization", lambda: reorganize_tree(dry_run=dry_run)),
        ("Cycle Repair", lambda: _run_cycle_repair()),
        ("Embedding Cache", lambda: run_embedding_job()),
    ]
    
    for i, (name, fn) in enumerate(jobs):
        _run_step(name, fn)
        if i < len(jobs) - 1:
            print(f"\n  Cooling down for {cooldown}s...")
            time.sleep(cooldown)
    
    print(f"\n{'='*50}")
    print(" ALL BACKGROUND JOBS COMPLETE")
    print(f"{'='*50}")

def _run_cycle_repair():
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        repair_cycles(session)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

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
    run_all_jobs(cooldown=cooldown, dry_run=is_dry_run)
