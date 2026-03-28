"""
MindCache Evaluation - Automatic Evidence-Session Grading Tool

For merged LongMemEval-M datasets where one DB contains memories from
multiple questions (potential conflicts).

Features:
- Provenance tracking: shows which session each retrieved memory came from
- Automatic grading: pass if a query evidence session appears in retrieved sources
- Extraction pre-check: verifies evidence was actually captured
- Clean results file: JSON + Markdown report with per-type scoring

Usage:
  python eval/eval_manual_check.py --dataset eval/data/longmemeval_m_merged.json
"""

import json
import argparse
import sys
import os
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.db_setup import engine, Topic, TriadBlock, EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory
from Database.db_manager import DatabaseManager
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# ─── Retrieval imports ────────────────────────────────────────────────────────
from retrieval.context_bridge import ContextBridge, RetrievalConfig, RetrievalContext
from retrieval.root_search import RootSearch
from retrieval.root_descent import RootDescent
from embedder import EmbeddingManager

_embedder = None
def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingManager()
    return _embedder


def normalize_session_ids(value) -> set:
    """Return a normalized set of source session IDs."""
    if value is None:
        return set()

    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value if v not in (None, "")}

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return set()

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {raw}

        if isinstance(parsed, list):
            return {str(v) for v in parsed if v not in (None, "")}
        if parsed in (None, ""):
            return set()
        return {str(parsed)}

    return {str(value)}


def format_session_ids(session_ids: set) -> str:
    """Format a set of source session IDs for console/report display."""
    if not session_ids:
        return "-"
    return json.dumps(sorted(session_ids), ensure_ascii=False)


def get_evidence_ids(test_case: dict) -> set:
    """Read evidence session IDs from either supported dataset field."""
    return normalize_session_ids(
        test_case.get("evidence_ids", test_case.get("evidence_session_ids", []))
    )


def load_dataset(path: str) -> dict:
    """Load merged dataset and extract metadata."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    metadata = data.get("metadata", {})
    sessions = data.get("sessions", [])
    test_cases = data.get("test_cases", data if isinstance(data, list) else [data])
    
    # Build session ID sets for fault classification
    base_session_ids = set()
    injected_session_ids = set()
    evidence_by_question = {}
    
    # All session IDs in the dataset
    all_session_ids = set(s["id"] for s in sessions)
    
    # Collect evidence session IDs per question
    for tc in test_cases:
        ev_ids = get_evidence_ids(tc)
        evidence_by_question[tc.get("question_id", "")] = ev_ids
        injected_session_ids.update(ev_ids)
    
    # Base = all sessions minus injected evidence
    base_session_ids = all_session_ids - injected_session_ids
    
    # Build session content lookup (for showing evidence text)
    session_content = {}
    for s in sessions:
        # Store first ~200 chars of session content for display
        turns = s.get("content", [])
        preview = ""
        for turn in turns[:4]:  # First 4 turns
            role = turn.get("role", "?")
            content = turn.get("content", "")[:150]
            preview += f"  {role}: {content}\n"
        session_content[s["id"]] = preview.strip()
    
    return {
        "metadata": metadata,
        "test_cases": test_cases,
        "base_session_ids": base_session_ids,
        "injected_session_ids": injected_session_ids,
        "evidence_by_question": evidence_by_question,
        "session_content": session_content,
    }


def extraction_precheck(test_cases: list) -> dict:
    """Check if evidence sessions were actually extracted into the DB."""
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    results = {}
    captured = 0
    missed = 0
    
    extracted_session_ids = set()
    for (source_session_id,) in db_session.query(TriadBlock.source_session_id).all():
        extracted_session_ids.update(normalize_session_ids(source_session_id))

    for tc in test_cases:
        q_id = tc.get("question_id", "")
        ev_ids = get_evidence_ids(tc)
        
        found_ids = []
        missing_ids = []
        
        for ev_id in ev_ids:
            if ev_id in extracted_session_ids:
                found_ids.append(ev_id)
                captured += 1
            else:
                missing_ids.append(ev_id)
                missed += 1
        
        results[q_id] = {
            "found": found_ids,
            "missing": missing_ids,
            "captured": len(found_ids) > 0
        }
    
    db_session.close()
    return {
        "per_question": results,
        "total_captured": captured,
        "total_missed": missed,
        "capture_rate": captured / max(captured + missed, 1)
    }


def retrieve_with_provenance(query: str, config: RetrievalConfig = None) -> dict:
    """Run retrieval and trace back each result to its source session."""
    if config is None:
        config = RetrievalConfig()
    
    embedder = get_embedder()
    bridge = ContextBridge(embedder, config)
    ctx = bridge.process(query)
    
    root_search = RootSearch(config, bridge)
    root_nodes = root_search.scan(ctx)

    if root_nodes is None:
        return {
            "root_name": None,
            "memories": [],
            "latency_s": 0,
        }
    descent = RootDescent(config, bridge)
    candidates = descent.descend(root_nodes, ctx)
    
    # Collect memories from candidates with provenance
    Session = sessionmaker(bind=engine)
    db_session = Session()
    
    memories = []
    memory_tables = [EpisodicMemory, UserMemory, KnowledgeMemory, DecisionMemory]
    
    for candidate in candidates:
        topic = db_session.get(Topic, candidate.topic_id)
        if not topic:
            continue
        
        for MemClass in memory_tables:
            mems = db_session.query(MemClass).filter(
                MemClass.topic_id == candidate.topic_id
            ).all()
            for mem in mems:
                # Get source session via TriadBlock
                source_session = None
                if mem.message:
                    source_session = mem.message.source_session_id
                
                memories.append({
                    "content": mem.content,
                    "type": MemClass.__tablename__.replace("memories_", ""),
                    "topic": topic.name,
                    "source_session_id": source_session,
                })
    
    db_session.close()
    
    return {
        "root_name": [root_node.name for root_node in root_nodes if root_nodes],
        "memories": memories,
    }


def classify_source(session_ids, evidence_ids: set, base_ids: set) -> str:
    """Classify a retrieved memory's source."""
    session_id_set = normalize_session_ids(session_ids)
    if not session_id_set:
        return "unknown"
    if session_id_set & evidence_ids:
        return "evidence"     # From this question's evidence
    if any(sid not in base_ids for sid in session_id_set):
        return "injected"     # From another question's injection (merge artifact)
    if session_id_set & base_ids:
        return "base"         # From the base haystack (noise)
    return "unknown"


def run_eval(dataset_path: str, output_dir: str = "eval/results"):
    """Main evaluation loop."""
    os.makedirs(output_dir, exist_ok=True)
    
    # Load dataset
    ds = load_dataset(dataset_path)
    test_cases = ds["test_cases"]
    
    print(f"\nLoaded {len(test_cases)} test questions.")
    
    # Extraction pre-check
    print("\n--- Extraction Pre-Check ---")
    ext_check = extraction_precheck(test_cases)
    print(f"Evidence captured: {ext_check['total_captured']}/{ext_check['total_captured'] + ext_check['total_missed']} "
          f"({ext_check['capture_rate']*100:.1f}%)")
    
    for q_id, info in ext_check["per_question"].items():
        if info["missing"]:
            print(f"  ⚠️  {q_id}: missing {info['missing']}")
    
    print(f"\n--- Starting Automatic Evaluation ---")
    print("A question PASSES when any retrieved memory points to one of that question's")
    print("evidence sessions. Otherwise it FAILS.\n")
    
    results = []
    
    try:
        for i, tc in enumerate(test_cases):
            q_id = tc.get("question_id", f"q_{i}")
            q_text = tc.get("question", "")
            q_type = tc.get("question_type", "unknown")
            expected = tc.get("answer", "")
            ev_ids = get_evidence_ids(tc)
            
            print(f"\n{'='*70}")
            print(f"  QUESTION {i+1}/{len(test_cases)} [{q_type}]  ID: {q_id}")
            print(f"{'='*70}")
            print(f"Q: {q_text}")
            print(f"Expected: {expected}")
            print(f"Evidence Sessions: {list(ev_ids)}")
            
            # Show evidence content preview
            print(f"\nEVIDENCE CONTENT:")
            for eid in ev_ids:
                preview = ds["session_content"].get(eid, "[not found]")
                print(f"  [{eid}]")
                for line in preview.split("\n")[:4]:
                    print(f"    {line}")
            
            # Check extraction status
            ext_info = ext_check["per_question"].get(q_id, {})
            if ext_info.get("missing"):
                print(f"\n  ⚠️  EXTRACTION MISS: Sessions {ext_info['missing']} not in DB")
            
            # Retrieve
            print(f"\nRetrieving context...")
            start = time.time()
            retrieval = retrieve_with_provenance(q_text)
            latency = time.time() - start
            
            print(f"\nRETRIEVED MEMORIES (Root: {retrieval['root_name']}, {latency:.2f}s):")
            print(f"{'-'*50}")
            
            if not retrieval["memories"]:
                print("  [NO MEMORIES RETRIEVED]")
            
            classified_memories = []
            retrieved_session_ids = set()
            for j, mem in enumerate(retrieval["memories"]):
                memory_session_ids = normalize_session_ids(mem["source_session_id"])
                retrieved_session_ids.update(memory_session_ids)
                source_type = classify_source(
                    memory_session_ids, ev_ids, ds["base_session_ids"]
                )
                
                # Icon
                icon = {"evidence": "OK", "base": "!", "injected": "!", "unknown": "?"}[source_type]
                label = {"evidence": "EVIDENCE", "base": "BASE", "injected": "INJECTED", "unknown": "UNKNOWN"}[source_type]
                source_display = format_session_ids(memory_session_ids)
                
                print(f"  {j+1}. [{mem['type']}] {mem['content'][:120]}")
                print(f"     -> Session(s): {source_display}  {icon} {label}")
                
                classified_memories.append({
                    "content": mem["content"],
                    "type": mem["type"],
                    "topic": mem["topic"],
                    "source_session": source_display,
                    "source_session_ids": sorted(memory_session_ids),
                    "source_type": source_type,
                })
            
            print(f"{'-'*50}")

            matched_evidence_sessions = sorted(ev_ids & retrieved_session_ids)
            grade = "pass" if matched_evidence_sessions else "fail"

            print("\nAUTOMATIC CHECK:")
            print(f"  Query evidence sessions:    {format_session_ids(ev_ids)}")
            print(f"  Retrieved source sessions:  {format_session_ids(retrieved_session_ids)}")
            print(f"  Matched evidence sessions:  {format_session_ids(set(matched_evidence_sessions))}")
            print(f"  Grade: {grade.upper()}")
            
            results.append({
                "question_id": q_id,
                "question_type": q_type,
                "question": q_text,
                "expected": expected,
                "evidence_sessions": list(ev_ids),
                "evidence_captured": ext_info.get("captured", False),
                "retrieved_source_sessions": sorted(retrieved_session_ids),
                "matched_evidence_sessions": matched_evidence_sessions,
                "retrieved_memories": classified_memories,
                "root_found": retrieval["root_name"],
                "grade": grade,
                "latency_s": latency,
            })
            
            print(f"  Recorded: {grade.upper()} ({len(results)} graded)")
            
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    
    _save_results(results, ds, ext_check, output_dir)


def _save_results(results: list, ds: dict, ext_check: dict, output_dir: str):
    """Save structured results JSON and human-readable report."""
    if not results:
        print("No results to save.")
        return
    
    # ─── Aggregate scores ─────────────────────────────────────────────────
    by_type = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "conflict": 0})
    
    for r in results:
        qt = r["question_type"]
        by_type[qt]["total"] += 1
        by_type[qt][r["grade"]] += 1
    
    total = len(results)
    passes = sum(1 for r in results if r["grade"] in ["pass", "conflict"])
    fails = sum(1 for r in results if r["grade"] == "fail")
    conflicts = sum(1 for r in results if r["grade"] == "conflict")
    
    summary = {
        "total": total,
        "pass": sum(1 for r in results if r["grade"] == "pass"),
        "fail": fails,
        "conflict": conflicts,
        "pass_rate": f"{passes / total * 100:.1f}%" if total else "N/A",
        "pass_rate_strict": f"{sum(1 for r in results if r['grade'] == 'pass') / total * 100:.1f}%" if total else "N/A",
    }
    
    output_data = {
        "timestamp": datetime.now().isoformat(),
        "dataset": ds.get("metadata", {}),
        "summary": summary,
        "by_question_type": dict(by_type),
        "extraction_check": {
            "evidence_captured": ext_check["total_captured"],
            "evidence_missed": ext_check["total_missed"],
            "capture_rate": f"{ext_check['capture_rate']*100:.1f}%",
        },
        "questions": results,
    }
    
    # Save JSON
    json_path = os.path.join(output_dir, "eval_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {json_path}")
    
    # Save Markdown report
    md_path = os.path.join(output_dir, "eval_report.md")
    _write_report(md_path, output_data)
    print(f"Report saved to {md_path}")
    
    # Print summary
    print(f"\n{'='*50}")
    print(f"  FINAL SCORE: {passes}/{total} ({summary['pass_rate']})")
    print(f"  Strict Pass: {summary['pass_rate_strict']}")
    print(f"  Conflicts:   {conflicts}")
    print(f"{'='*50}")
    
    print(f"\nBy Question Type:")
    for qt, scores in by_type.items():
        qt_pass = scores["pass"] + scores["conflict"]
        print(f"  {qt}: {qt_pass}/{scores['total']} "
              f"({qt_pass/scores['total']*100:.0f}% | {scores['conflict']} conflicts)")


def _write_report(path: str, data: dict):
    """Write a human-readable Markdown evaluation report."""
    s = data["summary"]
    ext = data["extraction_check"]
    
    lines = [
        "# MindCache Evaluation Report",
        f"**Date**: {data['timestamp'][:10]}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Questions | {s['total']} |",
        f"| Pass (incl. conflict) | {s['pass'] + s['conflict']} ({s['pass_rate']}) |",
        f"| Strict Pass | {s['pass']} ({s['pass_rate_strict']}) |",
        f"| Fail | {s['fail']} |",
        f"| Conflict | {s['conflict']} |",
        "",
        "## Extraction Quality",
        "",
        f"| Evidence Captured | {ext['evidence_captured']} |",
        f"|---|---|",
        f"| Evidence Missed | {ext['evidence_missed']} |",
        f"| Capture Rate | {ext['capture_rate']} |",
        "",
        "## Scores by Question Type",
        "",
        "| Type | Total | Pass | Fail | Conflict | Rate |",
        "|------|-------|------|------|----------|------|",
    ]
    
    for qt, scores in data["by_question_type"].items():
        qt_pass = scores["pass"] + scores["conflict"]
        rate = f"{qt_pass/scores['total']*100:.0f}%" if scores["total"] else "N/A"
        lines.append(f"| {qt} | {scores['total']} | {scores['pass']} | {scores['fail']} | {scores['conflict']} | {rate} |")
    
    lines.extend([
        "",
        "## Per-Question Details",
        "",
    ])
    
    for r in data["questions"]:
        lines.append(f"### {r['question_id']} [{r['question_type']}]")
        lines.append(f"**Q**: {r['question']}")
        lines.append(f"**Expected**: {r['expected']}")
        lines.append(f"**Grade**: {r['grade'].upper()}")
        lines.append(f"**Root**: {r.get('root_found', 'N/A')}")
        lines.append(f"**Evidence Sessions**: {format_session_ids(set(r.get('evidence_sessions', [])))}")
        lines.append(f"**Retrieved Source Sessions**: {format_session_ids(set(r.get('retrieved_source_sessions', [])))}")
        lines.append(f"**Matched Evidence Sessions**: {format_session_ids(set(r.get('matched_evidence_sessions', [])))}")
        lines.append(f"**Latency**: {r.get('latency_s', 0):.2f}s")
        lines.append("")
        
        if r.get("retrieved_memories"):
            lines.append("| # | Type | Content | Source Session | Source Type |")
            lines.append("|---|------|---------|---------------|------------|")
            for j, mem in enumerate(r["retrieved_memories"]):
                content_preview = mem["content"][:80].replace("|", "\\|")
                lines.append(f"| {j+1} | {mem['type']} | {content_preview} | {mem['source_session']} | {mem['source_type']} |")
        else:
            lines.append("*No memories retrieved.*")
        lines.append("")
    
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="eval/data/longmemeval_m_merged.json")
    parser.add_argument("--output-dir", default="eval/results")
    args = parser.parse_args()
    
    run_eval(args.dataset, args.output_dir)
