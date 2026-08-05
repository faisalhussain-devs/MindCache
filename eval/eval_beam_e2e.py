"""
eval/eval_beam_e2e.py — End-to-End BEAM Evaluation for MindCache
=================================================================

PURPOSE
-------
This script is the complete evaluation pipeline for testing MindCache against a
BEAM (Benchmark for Evaluating long-context AI Memory) conversation dataset,
leveraging the public MindCache library SDK.

It runs four sequential stages in one command:

  Stage 1 │ adapt_beam       — Convert a BEAM parquet → MindCache JSON format
  Stage 2 │ ingest_adapted   — Queue + run memory extraction using MindCache client
  Stage 3 │ run_retrieval_eval — Retrieve context, generate MindCache + collect mem0
             answers for all questions, save to JSON. Stops here — answers are safe.
  Stage 4 │ run_judge_eval   — Load the saved JSON, judge BOTH answers in one LLM
             call per question, write scores back into the same file, print summary.

LLM PROVIDER SUPPORT
--------------------
This script uses `litellm` for universal LLM access. Any provider supported by
litellm works out of the box — just set the appropriate env var and pass --model:

  Provider    | Env var              | Example --model value
  ------------|----------------------|-------------------------------
  OpenAI      | OPENAI_API_KEY       | gpt-5 / gpt-4o
  Anthropic   | ANTHROPIC_API_KEY    | claude-3-5-sonnet-20241022
  Google      | GEMINI_API_KEY       | gemini/gemini-2.0-flash
  Azure       | AZURE_API_KEY +      | azure/gpt-4o
              | AZURE_API_BASE       |
  Ollama      | (none needed)        | ollama/llama3

MEM0 COMPARISON
---------------
mem0 answers come from eval_results/beam_1m_top50_results.json, keyed as
"1M_<conv_index>". They are fetched in Stage 3 and stored in the results file
so Stage 4 can judge them without needing the original file again.
"""

import json
import re
import argparse
import sys
import os
import time
import logging
from datetime import datetime
from mindcache import MindCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eval_beam_e2e")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_litellm():
    try:
        import litellm
        return litellm
    except ImportError:
        log.error("litellm is required. Install with: pip install litellm")
        sys.exit(1)


def _validate_llm_key(model: str) -> bool:
    """
    Do a minimal probe call to verify the API key / model is reachable.
    Returns True on success, False on auth/config error.
    """
    litellm = _get_litellm()
    try:
        litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True
    except Exception as e:
        err = str(e).lower()
        if any(kw in err for kw in ("auth", "key", "api_key", "unauthorized", "forbidden", "invalid")):
            return False
        log.warning(f"[LLM] Probe returned non-auth error: {e}")
        return True


def llm_call(
    messages: list,
    model: str,
    max_tokens: int = 8000,
) -> str:
    """
    Generic LLM call via litellm. Returns the response text.
    temperature=0 for deterministic judging.
    """
    litellm = _get_litellm()
    response = litellm.completion(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def generate_answer(sys_prompt: str, retrieved_context: str, question: str,
                    model: str, max_tokens: int = 8000) -> str:
    """Generate an answer from MindCache retrieval context using the LLM."""
    messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                f"Retrieved context:\n{retrieved_context}\n\n"
                f"Question:\n{question}\n\nAnswer the question."
            ),
        },
    ]
    return llm_call(messages, model=model, max_tokens=max_tokens)


JUDGE_SYSTEM_PROMPT = """You are an expert, impartial memory-system evaluator.
You will be given a question, a ground-truth answer, a rubric of scoring criteria,
and two candidate answers (Answer A from MindCache, Answer B from mem0).

Your job is to score BOTH answers independently against every rubric point,
then produce an overall 0–1 score for each.

Rules:
- Judge each answer ONLY against the rubric and ground truth.
- For every rubric point, assign 1 (fully satisfied) or 0.5 (partially satisified) or 0 (not satisfied) to
  each answer separately.
- The overall score = (sum of satisfied rubric points) / (total rubric points).
  If there are no rubric points, use holistic judgment on a 0–1 scale.
- If an answer says it cannot answer / doesn't have information, check whether
  that is the CORRECT response (e.g. for abstention questions) — it may still
  score full marks.
- Be concise in your reasoning (1–2 sentences per answer is enough).

You MUST respond with ONLY a valid JSON object in exactly this structure
(no markdown fences, no extra keys):
{
  "rubric_scores": {
    "mindcache": [0_or_0.5_or_1, ...],
    "mem0":      [0_or_0.5_or_1, ...]
  },
  "overall_score": {
    "mindcache": <float 0.0–1.0>,
    "mem0":      <float 0.0–1.0>
  },
  "reasoning": {
    "mindcache": "<concise rationale>",
    "mem0":      "<concise rationale>"
  },
  "winner": "<mindcache|mem0|tie>"
}"""


def judge_answers(
    question_id:       str,
    question:          str,
    rubric:            list,
    expected_answer:   str,
    mindcache_answer:  str,
    mem0_answer:       str,
    model:             str,
) -> dict:
    """
    Send a single LLM call that simultaneously evaluates both the MindCache
    answer and the mem0 answer against the rubric and expected answer.

    Returns a dict with keys:
        rubric_scores   → {mindcache: [...], mem0: [...]}
        overall_score   → {mindcache: float, mem0: float}
        reasoning       → {mindcache: str, mem0: str}
        winner          → "mindcache" | "mem0" | "tie"
        error           → set only if the judge call failed
    """
    rubric_text = "\n".join(
        f"  {i+1}. {point}" for i, point in enumerate(rubric)
    ) if rubric else "  (no explicit rubric — use holistic judgment)"

    mem0_block = mem0_answer if mem0_answer else "(no answer available)"

    user_content = f"""QUESTION ID: {question_id}

QUESTION:
{question}

GROUND TRUTH ANSWER:
{expected_answer}

RUBRIC CRITERIA:
{rubric_text}

ANSWER A — MindCache:
{mindcache_answer}

ANSWER B — mem0:
{mem0_block}

Now score both answers and return the JSON object as instructed."""

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    try:
        raw = llm_call(messages, model=model, max_tokens=6000)

        # Strip markdown fences if the model wraps it anyway
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        verdict = json.loads(cleaned)

        # Normalise winner to lowercase
        verdict["winner"] = str(verdict.get("winner", "tie")).lower()

        log.info(
            f"  [Judge] mindcache={verdict['overall_score']['mindcache']:.2f} | "
            f"mem0={verdict['overall_score']['mem0']:.2f} | "
            f"winner={verdict['winner']}"
        )
        return verdict

    except json.JSONDecodeError as e:
        log.warning(f"  [Judge] JSON parse failed for {question_id}: {e}\n  Raw: {raw[:300]}")
        return {"error": f"json_parse_error: {e}", "raw_response": raw}
    except Exception as e:
        log.warning(f"  [Judge] Call failed for {question_id}: {e}")
        return {"error": str(e)}

def load_mem0_answers(mem0_results_path: str, conv_index: int) -> dict:
    """
    Load pre-computed mem0 answers for a specific conversation from
    beam_1m_top50_results.json.

    Returns a dict keyed by question text → full evaluation entry.
    Entries are keyed as "1M_<conv_index>_q<n>_<type>" in the source file.
    """
    if not mem0_results_path or not os.path.isfile(mem0_results_path):
        log.warning(f"[Mem0] Results file not found: {mem0_results_path} — skipping mem0 comparison.")
        return {}

    log.info(f"[Mem0] Loading results from: {mem0_results_path}")
    with open(mem0_results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    evals = data.get("evaluations", [])
    conv_evals = [e for e in evals if e.get("conversation_idx") == conv_index]

    if not conv_evals:
        log.warning(
            f"[Mem0] No entries for conversation_idx={conv_index}. "
            f"Available: {sorted(set(e.get('conversation_idx') for e in evals))}"
        )
        return {}

    log.info(f"[Mem0] Found {len(conv_evals)} mem0 entries for conv_idx={conv_index}")
    index = {}
    for e in conv_evals:
        q_text = e.get("question", "").strip()
        if q_text:
            index[q_text] = e
    return index


def get_mem0_entry(mem0_index: dict, question_text: str) -> dict | None:
    """Exact-match lookup, with a short-prefix fuzzy fallback."""
    q = question_text.strip()
    if q in mem0_index:
        return mem0_index[q]
    for key, entry in mem0_index.items():
        if q[:60] in key or key[:60] in q:
            return entry
    return None


# Stage 1 — Adapt BEAM parquet → MindCache JSON

def adapt_beam(parquet_path: str, output_path: str, conv_index: int = 7) -> dict:
    """
    Convert one BEAM conversation from parquet into MindCache ingestion format.

    Picks the conversation at `conv_index` (0-based row index).
    Returns the adapted dict and writes it to `output_path`.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas is required. Install with: pip install pandas pyarrow")
        sys.exit(1)
    import ast

    log.info(f"[Stage 1] Loading parquet: {parquet_path}")
    df = pd.read_parquet(parquet_path)

    if conv_index >= len(df):
        log.error(f"conv_index {conv_index} out of range — dataset has {len(df)} rows.")
        sys.exit(1)

    r       = df.iloc[conv_index]
    conv_id = r['conversation_id']
    chat    = r['chat']
    seed    = r['conversation_seed']

    def _flatten_session(sess):
        turns, seen = [], set()

        def _extract_turn(t):
            if isinstance(t, dict) and 'id' in t:
                if t['id'] not in seen:
                    seen.add(t['id'])
                    turns.append(t)
            elif hasattr(t, '__iter__') and not isinstance(t, (str, bytes, dict)):
                for item in t:
                    _extract_turn(item)

        if isinstance(sess, dict):
            for _, batches in sess.items():
                if not batches:
                    continue
                for batch in batches:
                    if isinstance(batch, dict):
                        for tl in batch.get('turns', []):
                            _extract_turn(tl)
                    elif hasattr(batch, '__iter__') and not isinstance(batch, (str, bytes, dict)):
                        for t in batch:
                            _extract_turn(t)
        elif hasattr(sess, '__iter__') and not isinstance(sess, (str, bytes, dict)):
            for item in sess:
                if isinstance(item, dict) and 'turns' in item:
                    for t in item.get('turns', []):
                        _extract_turn(t)
                else:
                    _extract_turn(item)
        return turns

    flat     = [_flatten_session(s) for s in chat]
    title    = seed.get('title', '?')    if isinstance(seed, dict) else '?'
    category = seed.get('category', '?') if isinstance(seed, dict) else '?'
    log.info(
        f"[Stage 1] Conv #{conv_id}: '{title}' ({category}) — "
        f"{len(chat)} sessions, {sum(len(s) for s in flat)} turns"
    )

    turn_to_session = {t['id']: si for si, turns in enumerate(flat) for t in turns}

    sessions = []
    for si, turns in enumerate(flat):
        ts = next((t['time_anchor'] for t in turns if isinstance(t, dict) and t.get('time_anchor')), None)
        clean_turns = []
        for t in turns:
            content = t['content']
            if '->->' in content:
                content = content.split('->->')[0].strip()
            clean_turns.append({"role": t['role'], "turn_id": t.get("id"), "content": content})
        sessions.append({"id": f"sess_{si}", "timestamp": ts, "content": clean_turns})
        log.info(f"  Session {si}: {len(clean_turns)} turns | ts={ts}")

    pq = r['probing_questions']
    if isinstance(pq, str):
        pq = ast.literal_eval(pq)

    def _flatten_ids(item):
        if item is None: return []
        if isinstance(item, (int, str)):
            try: return [int(item)]
            except ValueError: return []
        if isinstance(item, list):
            out = []
            for x in item: out.extend(_flatten_ids(x))
            return out
        if isinstance(item, dict):
            out = []
            for v in item.values(): out.extend(_flatten_ids(v))
            return out
        return []

    test_cases = []
    for cat, qs in pq.items():
        for i, q in enumerate(qs):
            ans_key = next(
                (k for k in ['answer', 'ideal_response', 'ideal_answer', 'ideal_summary']
                 if k in q and q[k]), None
            )
            raw_src = q.get('source_chat_ids', q.get('conversation_sessions'))
            ev_sess = set()
            if raw_src and raw_src != 'N/A':
                for tid in _flatten_ids(raw_src):
                    sid = turn_to_session.get(tid)
                    if sid is not None:
                        ev_sess.add(f"sess_{sid}")
            test_cases.append({
                "question_id":   f"{cat}_{i}",
                "question_type": cat,
                "question":      q['question'],
                "answer":        str(q[ans_key]) if ans_key else "",
                "answer_field":  ans_key or "compliance",
                "difficulty":    q.get('difficulty', ''),
                "rubric":        q.get('rubric', []),
                "session_ids":   sorted(ev_sess),
                "turn_ids":      raw_src,
            })

    output_data = {
        "metadata": {
            "source":          "BEAM",
            "conversation_id": str(conv_id),
            "title":           seed.get('title', '') if isinstance(seed, dict) else '',
            "category":        seed.get('category', '') if isinstance(seed, dict) else '',
            "parquet_index":   conv_index,
            "total_sessions":  len(sessions),
            "total_turns":     sum(len(s['content']) for s in sessions),
            "total_questions": len(test_cases),
        },
        "sessions":   sessions,
        "test_cases": test_cases,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    log.info(
        f"[Stage 1] Saved → {output_path}  "
        f"({len(sessions)} sessions, {len(test_cases)} test cases)"
    )
    return output_data


# Stage 2 — Ingest adapted JSON using the MindCache Library

def ingest_adapted(adapted_data: dict, db_path: str, user_id: str = "default"):
    """
    Ingest the adapted dataset sessions using the MindCache SDK client.
    Queues all turns under session groups and processes them dynamically.
    """
    mc = MindCache(db_path=db_path, enable_summarization=True)
    mc.reset(user_id=user_id)

    sessions = adapted_data.get("sessions", [])
    log.info(f"[Stage 2] Queuing {len(sessions)} sessions for user '{user_id}'")

    for session in sessions:
        ts = session.get("timestamp")
        timestamp_dt = None
        if ts:
            for fmt in ("%B-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    timestamp_dt = datetime.strptime(ts, fmt)
                    break
                except Exception:
                    continue
            if not timestamp_dt:
                try:
                    timestamp_dt = datetime.fromisoformat(ts)
                except Exception:
                    timestamp_dt = None
        messages = []
        for turn in session.get("content"):
            messages.append(turn)
            if turn.get("role") == "assistant":
                mc.add(messages=messages, user_id=user_id, timestamp=timestamp_dt)
                messages = []

    while True:
        log.info("[Stage 2] Processing queue (consolidation, batch embedding, extraction)…")
        out = mc.process_queue(user_id=user_id, limit=30, consolidation_max_tokens=4000)
        log.info("[Stage 2] Ingestion complete.")
        if out == 0 or out == {"success": 0, "failed": 0}:
            break

# Stage 3 — Retrieval: generate MindCache answers + collect mem0 answers

def run_retrieval_eval(
    test_cases: list,
    db_path:    str,
    output_dir: str,
    conv_id:    str,
    user_id:    str = "default",
    limit:      int = None,
    model:      str = "gpt-5",
    mem0_index: dict = None,
) -> str:
    """
    Stage 3: For every test question —
      1. Retrieve context from MindCache and generate an answer via the LLM.
      2. Look up the corresponding mem0 answer from the pre-loaded index.
      3. Store both answers (no scoring yet) in a result entry.

    Saves results to: <output_dir>/beam_conv<conv_id>_results.json
    """
    os.makedirs(output_dir, exist_ok=True)
    if limit:
        test_cases = test_cases[:limit]
    if mem0_index is None:
        mem0_index = {}

    log.info(f"[Stage 3] MindCache db={db_path} | user={user_id} | model={model}")
    log.info(f"[Stage 3] mem0 comparison: {'enabled' if mem0_index else 'disabled'}")

    mc = MindCache(db_path=db_path, enable_summarization=True)

    results = []
    for i, tc in enumerate(test_cases):
        q_id     = tc.get("question_id",   f"q_{i}")
        q_type   = tc.get("question_type", "unknown")
        q_text   = tc.get("question",      "")
        expected = tc.get("answer", tc.get("expected", ""))
        rubric   = tc.get("rubric",        [])
        log.info(f"[Stage 3] [{i+1}/{len(test_cases)}] {q_text[:80]}…")

        # MindCache retrieval + answer generation
        t0         = time.time()
        result     = mc.search(q_text, user_id=user_id)
        context    = result.context
        query_type = result.query_type
        sys_prompt = result.system_hint
        lat        = round(time.time() - t0, 3)

        mindcache_answer = generate_answer(sys_prompt, context, q_text, model=model)

        log.info(f"  [MindCache] latency={lat}s | context={len(context)} chars")
        log.info(f"  [MindCache] answer={mindcache_answer[:100]!r}…")

        # mem0 answer lookup
        mem0_entry  = get_mem0_entry(mem0_index, q_text) if mem0_index else None
        mem0_answer = None
        mem0_q_id   = None

        if mem0_entry:
            top50       = mem0_entry.get("cutoff_results", {}).get("top_50", {})
            mem0_answer = top50.get("generated_answer")
            mem0_q_id   = mem0_entry.get("question_id")
            log.info(f"  [Mem0]     answer={str(mem0_answer)[:100]!r}…")
        else:
            log.info(f"  [Mem0]     no matching entry found.")

        # Store raw answers (no judge scores yet)
        results.append({
            "question_id":   q_id,
            "question_type": q_type,
            "detected_type": query_type,
            "question":      q_text,
            "expected":      expected,
            "rubric":        rubric,
            "mindcache": {
                "generated_answer":  mindcache_answer,
                "retrieval_latency": lat,
                "context_chars":     len(context),
            },
            "mem0": {
                "question_id":      mem0_q_id,
                "generated_answer": mem0_answer,
            } if mem0_entry else None,
            "judge": None,
        })
        time.sleep(0.5)

    out_path = os.path.join(output_dir, f"beam_conv{conv_id}_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info(f"[Stage 3]  {len(results)} answers saved → {out_path}")
    log.info("[Stage 3] Answers are safe. Run Stage 4 to judge them.")
    return out_path


# Stage 4 — Judge: load saved results, score both answers, re-save + summarise

def run_judge_eval(results_path: str, model: str) -> None:
    """
    Stage 4: Load the Stage-3 results file, judge every entry with one LLM
    call per question, write the verdict back into the same file, then print
    the final comparative summary.

    The judge call sends question + rubric + expected answer + MindCache answer
    + mem0 answer in a single prompt, asking the LLM to score both answers
    simultaneously. This eliminates positional bias and keeps scoring consistent.
    """
    if not os.path.isfile(results_path):
        log.error(f"[Stage 4] Results file not found: {results_path}")
        sys.exit(1)

    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    total    = len(results)
    skipped  = 0
    log.info(f"[Stage 4] Judging {total} questions with model={model}")
    log.info(f"[Stage 4] Source file: {results_path}")

    for i, r in enumerate(results):
        q_id             = r.get("question_id", f"q_{i}")
        question         = r.get("question", "")
        rubric           = r.get("rubric", [])
        expected         = r.get("expected", "")
        mindcache_answer = (r.get("mindcache") or {}).get("generated_answer", "")
        mem0_block       = r.get("mem0") or {}
        mem0_answer      = mem0_block.get("generated_answer", None)

        log.info(f"[Stage 4] [{i+1}/{total}] Judging: {question[:70]}…")

        if not mindcache_answer or not mem0_answer:
            log.warning(f"  [Judge] One of the answers are empty for {q_id} — skipping.")
            r["judge"] = {"error": "one of answers empty", "skipped": True}
            skipped += 1
        else:
            verdict = judge_answers(
                question_id=q_id,
                question=question,
                rubric=rubric,
                expected_answer=expected,
                mindcache_answer=mindcache_answer,
                mem0_answer=mem0_answer,
                model=model,
            )
            r["judge"] = verdict

        # Re-save after every question — partial progress is always on disk
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        time.sleep(0.3)   # small back-off to avoid rate limits

    log.info(f"[Stage 4]  Judging complete — {total - skipped}/{total} scored.")
    log.info(f"[Stage 4] Results updated → {results_path}")

    _print_summary(results)


# Summary printer — uses fresh Stage-4 judge scores

def _print_summary(results: list) -> None:
    """
    Print a side-by-side MindCache vs mem0 comparison using the Stage-4
    judge scores. Groups by question type for detailed breakdown.
    """
    SEP = "=" * 72

    judged = [r for r in results if r.get("judge") and not r["judge"].get("error") and not r["judge"].get("skipped")]

    mc_scores   = [r["judge"]["overall_score"]["mindcache"] for r in judged]
    mem0_scores = [r["judge"]["overall_score"]["mem0"]      for r in judged]
    winners     = [r["judge"].get("winner", "tie")          for r in judged]

    mc_wins   = winners.count("mindcache")
    mem0_wins = winners.count("mem0")
    ties      = winners.count("tie")

    avg_mc   = sum(mc_scores)   / len(mc_scores)   if mc_scores   else 0.0
    avg_mem0 = sum(mem0_scores) / len(mem0_scores) if mem0_scores else 0.0

    log.info(SEP)
    log.info("  FINAL EVALUATION SUMMARY  —  MindCache vs mem0")
    log.info(SEP)
    log.info(f"  Total questions  : {len(results)}")
    log.info(f"  Successfully judged : {len(judged)}")
    log.info(f"  Errors / skipped : {len(results) - len(judged)}")
    log.info("")
    log.info(f"  {'System':<20} {'Avg Score':>10}   {'Wins':>6}   {'Win %':>7}")
    log.info(f"  {'-'*20} {'-'*10}   {'-'*6}   {'-'*7}")
    log.info(f"  {'MindCache':<20} {avg_mc:>10.3f}   {mc_wins:>6}   {mc_wins/len(judged)*100 if judged else 0:>6.1f}%")
    log.info(f"  {'mem0':<20} {avg_mem0:>10.3f}   {mem0_wins:>6}   {mem0_wins/len(judged)*100 if judged else 0:>6.1f}%")
    log.info(f"  {'Ties':<20} {'':>10}   {ties:>6}   {ties/len(judged)*100 if judged else 0:>6.1f}%")
    log.info("")

    from collections import defaultdict
    by_type: dict = defaultdict(lambda: {"mc": [], "mem0": [], "winners": []})
    for r in judged:
        qt = r.get("question_type", "unknown")
        by_type[qt]["mc"].append(r["judge"]["overall_score"]["mindcache"])
        by_type[qt]["mem0"].append(r["judge"]["overall_score"]["mem0"])
        by_type[qt]["winners"].append(r["judge"].get("winner", "tie"))

    log.info(f"  {'Question Type':<30} {'MC Avg':>8}  {'M0 Avg':>8}  {'MC W':>5}  {'M0 W':>5}  {'Tie':>5}")
    log.info(f"  {'-'*30} {'-'*8}  {'-'*8}  {'-'*5}  {'-'*5}  {'-'*5}")
    for qt in sorted(by_type):
        d   = by_type[qt]
        n   = len(d["mc"])
        mc  = sum(d["mc"])   / n
        m0  = sum(d["mem0"]) / n
        mcw = d["winners"].count("mindcache")
        m0w = d["winners"].count("mem0")
        tw  = d["winners"].count("tie")
        log.info(f"  {qt:<30} {mc:>8.3f}  {m0:>8.3f}  {mcw:>5}  {m0w:>5}  {tw:>5}")

    log.info(SEP)
    overall_winner = (
        "MindCache" if avg_mc > avg_mem0 else
        "mem0"      if avg_mem0 > avg_mc else
        "TIE"
    )
    log.info(f"  ► Overall winner: {overall_winner}  "
             f"(MindCache {avg_mc:.3f} vs mem0 {avg_mem0:.3f})")
    log.info(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="End-to-end BEAM evaluation for MindCache using library SDK.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
STAGES
  By default all 4 stages run in sequence.
  Use --skip-ingest to jump straight to Stage 3 (assumes DB is already populated).
  Use --skip-retrieval to jump straight to Stage 4 judging (assumes Stage 3 results exist).

LLM MODEL EXAMPLES
  --model gpt-5                        (OpenAI, needs OPENAI_API_KEY)
  --model gpt-4o                       (OpenAI, needs OPENAI_API_KEY)
  --model claude-3-5-sonnet-20241022   (Anthropic, needs ANTHROPIC_API_KEY)
  --model gemini/gemini-2.0-flash      (Google, needs GEMINI_API_KEY)
  --model azure/gpt-4o                 (Azure, needs AZURE_API_KEY + AZURE_API_BASE)
  --model ollama/llama3                (Ollama, no key needed, runs locally)

  Use --judge-model to use a different (often stronger) model for Stage 4 judging.
  If omitted, the same --model is used for both answering and judging.

BEAM CONVERSATION INDEX
  --conv-index is 0-based. Valid range: 0–34 for the 1M dataset.
  The mem0 comparison file is keyed as "1M_<conv_index>".
        """,
    )
    parser.add_argument("--parquet",        default="BEAM/1M-00000-of-00001.parquet")
    parser.add_argument("--conv-index",     type=int, default=34,
                        help="0-based conversation index (0–34). Default: 34")
    parser.add_argument("--db-path",        default="mindcache.db")
    parser.add_argument("--output-dir",     default="eval/eval_results")
    parser.add_argument("--user-id",        default="default")
    parser.add_argument("--adapted-json",   default=None)
    parser.add_argument("--skip-ingest",    action="store_true",
                        help="Skip Stages 1 & 2 (parquet adaptation + ingestion).")
    parser.add_argument("--skip-retrieval", action="store_true",
                        help="Skip Stage 3 and go straight to Stage 4 judging. "
                             "Requires the Stage-3 results file to already exist.")
    parser.add_argument("--skip-judge",     action="store_true",
                        help="Skip Stage 4 judging (only run retrieval, save answers).")
    parser.add_argument("--limit",          type=int, default=None,
                        help="Limit evaluation to first N questions (for quick tests).")
    parser.add_argument("--model",          default=None,
                        help="LLM model for answer generation (Stage 3). "
                             "Auto-detected from env vars if not set.")
    parser.add_argument("--judge-model",    default=None,
                        help="LLM model for judging (Stage 4). Defaults to --model.")
    parser.add_argument("--mem0-results",   default=None,
                        help="Path to mem0 pre-computed results JSON. "
                             "Defaults to <output-dir>/beam_1m_top50_results.json")
    parser.add_argument("--skip-key-check", action="store_true",
                        help="Skip LLM API key validation probe at startup.")
    args = parser.parse_args()

    # Resolve answering model

    if args.model:
        model = args.model
    else:
        if os.getenv("OPENAI_API_KEY"):
            model = "gpt-5"
        elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            model = "gemini/gemini-2.5-flash"
        elif os.getenv("ANTHROPIC_API_KEY"):
            model = "claude-3-5-sonnet-20241022"
        elif os.getenv("AZURE_API_KEY"):
            model = "azure/gpt-4o"
        else:
            model = "ollama/llama3"
            log.warning("[LLM] No API key detected — defaulting to ollama/llama3.")
            
    log.info(f"[LLM] Answering model : {model}")

    # Resolve judge model
    judge_model = args.judge_model or model
    log.info(f"[LLM] Judge model     : {judge_model}")

    # Validate API key
    if not args.skip_key_check:
        log.info("[LLM] Validating API key…")
        if _validate_llm_key(model):
            log.info("[LLM] API key validated OK.")
        else:
            log.error(
                f"[LLM] Validation FAILED for model '{model}'. "
                "Check your API key. Use --skip-key-check to bypass."
            )
            sys.exit(1)

    # Resolve mem0 path
    if args.mem0_results:
        mem0_results_path = args.mem0_results
    else:
        mem0_results_path = os.path.join(args.output_dir, "beam_1m_top50_results.json")
        if not os.path.isfile(mem0_results_path):
            alt = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "eval", "eval_results", "beam_1m_top50_results.json"
            )
            if os.path.isfile(alt):
                mem0_results_path = alt

    # Validate conv index
    if not (0 <= args.conv_index <= 34):
        log.error(f"--conv-index must be between 0 and 34 (got {args.conv_index}).")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    if args.adapted_json is None:
        args.adapted_json = os.path.join(
            args.output_dir, f"beam_adapted_conv{args.conv_index}.json"
        )

    # Stages 1 & 2
    if not args.skip_ingest and not args.skip_retrieval:
        adapted_data = adapt_beam(args.parquet, args.adapted_json, args.conv_index)
        ingest_adapted(adapted_data=adapted_data, db_path=args.db_path, user_id=args.user_id)
    elif not args.skip_retrieval:
        log.info(f"[Stage 1-2] Skipped — loading: {args.adapted_json}")
        with open(args.adapted_json, "r", encoding="utf-8") as f:
            adapted_data = json.load(f)

    # Stage 3
    if not args.skip_retrieval:
        conv_id    = adapted_data.get("metadata", {}).get("conversation_id", str(args.conv_index))
        test_cases = adapted_data.get("test_cases", [])
        mem0_index = load_mem0_answers(mem0_results_path, args.conv_index)

        results_path = run_retrieval_eval(
            test_cases=test_cases,
            db_path=args.db_path,
            output_dir=args.output_dir,
            conv_id=conv_id,
            user_id=args.user_id,
            limit=args.limit,
            model=model,
            mem0_index=mem0_index,
        )
    else:
        import glob
        pattern = os.path.join(args.output_dir, f"beam_conv*_results.json")
        candidates = [
            p for p in glob.glob(pattern)
            if "adapted" not in p and "matched" not in p and "prompts" not in p
        ]
        if not candidates:
            log.error(
                f"[Stage 4] --skip-retrieval set but no results file found in "
                f"{args.output_dir}. Run Stage 3 first."
            )
            sys.exit(1)
        results_path = max(candidates, key=os.path.getmtime)
        log.info(f"[Stage 3] Skipped — using existing results: {results_path}")

    # Stage 4
    if not args.skip_judge:
        log.info("")
        log.info("─" * 60)
        log.info("[Stage 4] Starting judge evaluation…")
        log.info("─" * 60)
        run_judge_eval(results_path=results_path, model=judge_model)
    else:
        log.info("[Stage 4] Skipped (--skip-judge). Answers saved, no scoring done.")

    log.info("=" * 60)
    log.info("Pipeline complete.")
    log.info(f"Results → {results_path}")
    log.info("=" * 60)
