import os
import sys
import subprocess
import time

def get_master_keys():
    keys_env = os.environ.get("GEMINI_MASTER_KEYS")
    if not keys_env:
        # Fallback to see if they just have GEMINI_API_KEYS
        keys_env = os.environ.get("GEMINI_API_KEYS")
        if not keys_env:
            print("FATAL: Please set the GEMINI_MASTER_KEYS environment variable with a comma-separated list of your API keys.")
            sys.exit(1)
    
    keys = [k.strip() for k in keys_env.split(",") if k.strip()]
    print(f"[Orchestrator] Loaded {len(keys)} master API keys.")
    return keys

def run_script_with_keys(script_path, keys, args=[]):
    """Runs a target python script, passing it the designated subset of API keys."""
    env = os.environ.copy()
    env["GEMINI_API_KEYS"] = ",".join(keys)
    
    cmd = [sys.executable, script_path] + args
    
    print(f"[Orchestrator] Running: {' '.join(cmd)}")
    print(f"[Orchestrator] Passed {len(keys)} API keys to this process.")
    
    start_time = time.time()
    result = subprocess.run(cmd, env=env)
    elapsed = time.time() - start_time
    
    print(f"\n[Orchestrator] Process finished in {elapsed:.1f}s with exit code {result.returncode}")
    return result.returncode

def main():
    keys = get_master_keys()
    key_idx = 0
    
    while key_idx < len(keys):
        ingest_keys = keys[key_idx:key_idx+2]
        key_idx += 2
                
        if not ingest_keys:
            break
            
        print(f"\n STARTING INGEST PHASE")
        # Exit code 8 means it hit the Quota Exhaustion block gracefully. 
        # Exit code 0 means it finished all pending jobs cleanly.
        ret_code = run_script_with_keys("e:/MindCache/eval/ingest_api.py", ingest_keys, args=["run"])
        
        # If it finished all jobs cleanly (0), we still want to run reorganize before stopping.
        # If it crashed for some other unknown error (!= 8 and != 0), maybe we should stop to be safe.
        if ret_code != 0 and ret_code != 8:
            print(f"[Orchestrator] Ingest script failed with unexpected error {ret_code}. Stopping pipeline.")
            break
            
        # 2. Grab 1 key for the REORGANIZE phase
        if key_idx >= len(keys):
            print("\n[Orchestrator] Out of keys for the reorganize phase! Stopping.")
            break
            
        reorg_key = keys[key_idx]
        
        print(f"\n STARTING REORGANIZE PHASE")
        ret_code = run_script_with_keys("e:/MindCache/reorganize_tree.py", [reorg_key])
        
        if ret_code != 0:
            print(f"[Orchestrator] Reorganize script failed with error {ret_code}. Continuing to next loop anyway to not block ingest...")
            
    print("\n[Orchestrator] All master keys have been exhausted or pipeline finished cleanly!")

if __name__ == "__main__":
    main()
