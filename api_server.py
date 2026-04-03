"""
MindCache API Server
====================
Lightweight FastAPI server that exposes MindCache logic to the browser extension.

Endpoints:
  GET  /health                  → ping / connection check
  GET  /tree?depth=3            → topic hierarchy for tree UI
  GET  /node/{id}/memories      → raw memories for a specific leaf node
  POST /retrieve                → run ActivePathRetrieval for a query
  POST /ingest                  → add a prompt/response pair to the processing queue
  GET  /queue/status            → how many jobs are pending/processing/failed

Run:
  python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations
import numpy as np
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker
from collections import defaultdict
from functools import lru_cache
from Database.db_setup import engine, Topic, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory, ProcessingJob
from Database.db_manager import DatabaseManager
from dataclasses import dataclass, field
import time


# App setup
app = FastAPI(title="MindCache API", version="1.0.0")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Allow requests from Chrome extensions and localhost dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Chrome extensions use chrome-extension:// origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

db_manager = DatabaseManager()
Session = sessionmaker(bind=engine)

# ── Pipeline control ───────────────────────────────────────────────────────────
import threading
import time as _time

_scheduler_running = False
_scheduler_mode = None

def _lazy_run_step(name, fn):
    """Inline version of _run_step to avoid importing background_scheduler at module level."""
    print(f" RUNNING: {name}")
    try:
        fn()
    except Exception as e:
        print(f"  [ERROR] {name} failed: {e}")

@app.post("/run-scheduler")
def trigger_full_scheduler(cooldown: int = 30):
    """Manual trigger: runs ALL 5 steps (Extract → Reorg → Decision → Summary → Embed)."""
    global _scheduler_running, _scheduler_mode
    if _scheduler_running:
        return {"status": "already_running", "mode": _scheduler_mode}

    def run():
        global _scheduler_running, _scheduler_mode
        _scheduler_running = True
        _scheduler_mode = "full"
        try:
            from Database.background_scheduler import run_all_jobs
            run_all_jobs(cooldown=cooldown)
        finally:
            _scheduler_running = False
            _scheduler_mode = None

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "mode": "full"}

@app.post("/run-tier1")
def trigger_tier1():
    """Auto-mode tier 1: Extract memories + Decision Analyzer only."""
    global _scheduler_running, _scheduler_mode
    if _scheduler_running:
        return {"status": "already_running", "mode": _scheduler_mode}

    def run():
        global _scheduler_running, _scheduler_mode
        _scheduler_running = True
        _scheduler_mode = "tier1"
        try:
            _lazy_run_step("Memory Extractor", lambda: db_manager.process_memory())
            _time.sleep(10)
            _lazy_run_step("Decision Analyzer", lambda: db_manager.run_decision_state_analyzer())
        finally:
            _scheduler_running = False
            _scheduler_mode = None

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "mode": "tier1"}

@app.post("/run-tier2")
def trigger_tier2():
    """Auto-mode tier 2: Full pipeline (reorg + summaries + embeddings)."""
    global _scheduler_running, _scheduler_mode
    if _scheduler_running:
        return {"status": "already_running", "mode": _scheduler_mode}

    def run():
        global _scheduler_running, _scheduler_mode
        _scheduler_running = True
        _scheduler_mode = "tier2"
        try:
            from Database.background_scheduler import run_all_jobs
            run_all_jobs(cooldown=30)
        finally:
            _scheduler_running = False
            _scheduler_mode = None

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "mode": "tier2"}

@app.get("/scheduler-status")
def scheduler_status():
    """Check if the background scheduler is currently running."""
    return {"running": _scheduler_running, "mode": _scheduler_mode}

@app.get("/queue/status")
def queue_status():
    """Return counts of pending/failed/processing jobs in the extraction queue."""
    from Database.db_setup import ProcessingJob
    session = Session()
    try:
        pending = session.query(ProcessingJob).filter(ProcessingJob.status == 'pending').count()
        failed = session.query(ProcessingJob).filter(ProcessingJob.status == 'failed').count()
        processing = session.query(ProcessingJob).filter(ProcessingJob.status == 'processing').count()
        return {
            "queue": {
                "pending": pending,
                "failed": failed,
                "processing": processing,
                "total": pending + failed + processing
            }
        }
    finally:
        session.close()

# Lazy-load the retrieval pipeline (heavy — loads embedding model once)
_retrieval_pipeline = None

def get_pipeline():
    global _retrieval_pipeline
    if _retrieval_pipeline is None:
        from retrieval.active_path import ActivePathRetrieval
        _retrieval_pipeline = ActivePathRetrieval()
    return _retrieval_pipeline

@dataclass
class Tree:
    children_map: dict = field(default_factory=dict)
    topic_by_id: dict = field(default_factory=dict)
    parent_map: dict = field(default_factory=dict)

    def __init__(self, session):
        self.children_map = defaultdict(list)
        self.parent_map = defaultdict(list)
        self.topic_by_id = {None: None}
        self.embedding_cache = {}
        topics = session.query(Topic).all()
        for t in topics:
            self.topic_by_id[t.id] = t
            self.children_map[t.parent_id].append(t)
            if t.embedding:
                self.embedding_cache[t.id] = np.frombuffer(t.embedding, dtype=np.float32).copy()
        self.parent_map = {t.id: self.topic_by_id.get(t.parent_id) for t in topics}

@lru_cache()
def get_tree_cache():
    session = Session()
    try:
        return Tree(session)
    finally:
        session.close()

def _serialize_node(node_id: int, depth: int, max_depth: int) -> dict:
    tree = get_tree_cache()
    node = tree.topic_by_id.get(node_id)
    if node is None:
        raise HTTPException(404, f"Topic {node_id} not found")

    children = tree.children_map.get(node_id, [])

    result = {
        "id": node_id,
        "parent_id": node.parent_id,
        "name": node.name,
        "level": node.level,
        "description": node.description or "",
        "has_children": bool(children),
        "children": [],
    }

    if depth < max_depth:
        result["children"] = [
            _serialize_node(child.id, depth + 1, max_depth)
            for child in children
        ]

    return result

# Request / Response models
class IngestRequest(BaseModel):
    prompt: str
    response: str
    next_prompt: str | None

class RetrieveRequest(BaseModel):
    query: str
    last_msg: str | None = None
    prev_msg: str | None = None
    selected_nodes_by_level: dict[str | int, list[int]] = Field(default_factory=dict)

# Helpers

def _format_memories(session, topic_id: int) -> list[dict]:
    """Fetch all 4 memory types for a topic and return as a list of dicts."""
    memories = []

    for Model, label in [
        (EpisodicMemory, "episodic"),
        (UserMemory, "user"),
        (KnowledgeMemory, "knowledge"),
    ]:
        rows = session.query(Model).filter(Model.topic_id == topic_id).all()
        for m in rows:
            memories.append({
                "type": label,
                "content": m.content or "",
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            })

    decisions = session.query(DecisionMemory).filter(
        DecisionMemory.topic_id == topic_id
    ).all()
    for d in decisions:
        memories.append({
            "type": "decision",
            "content": d.content or "",
            "status": d.status,
            "context": d.context,
            "timestamp": d.timestamp.isoformat() if d.timestamp else None,
        })

    return memories


@app.get("/health")
def health():
    return {"status": "ok", "service": "MindCache API"}


@app.get("/")
def graph_ui():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Graph UI not found")
    return FileResponse(index_path)


@app.get("/graph")
def graph_ui_alias():
    graph_path = STATIC_DIR / "graph.html"
    if not graph_path.exists():
        raise HTTPException(status_code=404, detail="Graph explorer UI not found")
    return FileResponse(graph_path)


@app.get("/explore")
def explorer_ui():
    explorer_path = STATIC_DIR / "explorer.html"
    if not explorer_path.exists():
        raise HTTPException(status_code=404, detail="Root explorer UI not found")
    return FileResponse(explorer_path)


@app.get("/tree")
def get_tree(depth: int = Query(default=3, ge=1, le=10)):
    """Return the topic hierarchy up to `depth` levels deep."""
    tree = get_tree_cache()
    return {
            "depth": depth,
            "roots": [_serialize_node(root.id, depth=0, max_depth=depth) for root in tree.children_map.get(None)],
        }


@app.get("/node/{node_id}/tree")
def get_node_tree(node_id: int, depth: int = Query(default=3, ge=1, le=10)):
    """Return a node plus its descendants up to `depth` levels deep."""
    return {
        "depth": depth,
        "node": _serialize_node(node_id, depth=0, max_depth=depth),
    }


@app.get("/node/{node_id}/memories")
def get_node_memories(node_id: int):
    """Return all memories stored under a specific topic node."""
    session = Session()
    try:
        node = session.get(Topic, node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Topic {node_id} not found")
        memories = _format_memories(session, node_id)
        return {
            "node_id": node_id,
            "name": node.name,
            "description": node.description,
            "memory_count": len(memories),
            "memories": memories,
        }
    finally:
        session.close()


@app.post("/retrieve")
def retrieve(req: RetrieveRequest):
    """
    Run the full MindCache retrieval pipeline for a query.
    Returns the retrieved memory context as a string.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    try:
        start = time.time()
        pipeline = get_pipeline()
        result = pipeline.retrieve(
            current_prompt=req.query,
            last_msg=req.last_msg,
            prev_msg=req.prev_msg,
            selected_nodes_by_level=req.selected_nodes_by_level,
        )
        end = time.time()
        print("Execution time:", end - start, "seconds")
        return {"context": result.context, "trace": result.trace}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest")
def ingest(req: IngestRequest):
    """
    Add a scraped prompt/response pair to the ProcessingJob queue.
    The background extraction job will pick it up and extract memories.
    """
    if not req.prompt.strip() or not req.response.strip():
        raise HTTPException(status_code=400, detail="Both prompt and response are required")
    try:
        db_manager.add_to_queue(
            prompt=req.prompt,
            response=req.response,
            next_prompt=req.next_prompt,
        )
        return {"status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/queue/status")
def queue_status():
    """Returns how many jobs are in each state in the processing queue."""
    session = Session()
    try:
        from sqlalchemy import func
        counts = (
            session.query(ProcessingJob.status, func.count(ProcessingJob.id))
            .group_by(ProcessingJob.status)
            .all()
        )
        return {"queue": {status: count for status, count in counts}}
    finally:
        session.close()


# ── Dev entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="127.0.0.1", port=8000, reload=True)
