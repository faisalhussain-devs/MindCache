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

import os
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from typing import Optional

from Database.db_setup import engine, Topic, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory, ProcessingJob
from Database.db_manager import DatabaseManager

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="MindCache API", version="1.0.0")

# Allow requests from Chrome extensions and localhost dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Chrome extensions use chrome-extension:// origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db_manager = DatabaseManager()
Session = sessionmaker(bind=engine)

# ── Lazy-load the retrieval pipeline (heavy — loads embedding model once) ────
_retrieval_pipeline = None

def get_pipeline():
    global _retrieval_pipeline
    if _retrieval_pipeline is None:
        from retrieval.active_path import ActivePathRetrieval
        _retrieval_pipeline = ActivePathRetrieval()
    return _retrieval_pipeline


# ── Request / Response models ─────────────────────────────────────────────────
class IngestRequest(BaseModel):
    prompt: str
    response: str

class RetrieveRequest(BaseModel):
    query: str
    last_msg: Optional[str] = None
    prev_msg: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _serialize_node(node: Topic, depth: int, max_depth: int) -> dict:
    """Recursively serialize a topic node to a dict for the tree UI."""
    result = {
        "id": node.id,
        "name": node.name,
        "level": node.level,
        "description": node.description,
        "has_children": bool(node.children),
        "children": [],
    }
    if depth < max_depth and node.children:
        result["children"] = [
            _serialize_node(child, depth + 1, max_depth)
            for child in node.children
        ]
    return result


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


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "MindCache API"}


@app.get("/tree")
def get_tree(depth: int = Query(default=3, ge=1, le=10)):
    """Return the topic hierarchy up to `depth` levels deep."""
    session = Session()
    try:
        roots = session.query(Topic).filter(Topic.level == 0).order_by(Topic.name).all()
        return {
            "depth": depth,
            "roots": [_serialize_node(root, depth=0, max_depth=depth) for root in roots],
        }
    finally:
        session.close()


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
        pipeline = get_pipeline()
        result = pipeline.retrieve(
            current_prompt=req.query,
            last_msg=req.last_msg,
            prev_msg=req.prev_msg,
        )
        return {"context": result.context}
    except Exception as e:
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
            next_prompt=None,
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
