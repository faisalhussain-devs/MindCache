import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from mindcache.Database.db_setup import (
    DecisionMemory, EpisodicMemory, KnowledgeMemory, UserMemory
)
from mindcache.Memory_extract.summary_extractor import Summary_Extractor
import logging
logger = logging.getLogger(__name__)


ANALYSIS_PROMPT = """You are the Decision State Analyzer for a memory database.
You will receive ALL decisions stored under a single topic, plus supporting memories (facts, events, user preferences).

Your job: Determine the current STATUS of each decision.

### STATUS OPTIONS
- "active"      → This decision is currently in effect.
- "superseded"  → A newer decision on the same matter replaced this one.
- "rejected"    → Evidence shows this decision was wrong or abandoned.
- "conditional" → This decision only applies under specific conditions.
- "inactive"    → No longer relevant but not explicitly rejected.

### RULES
1. If two decisions contradict each other, the NEWER one (higher ID) is usually "active" and the older one is "superseded" — unless supporting evidence says otherwise.
2. Use facts and episodic memories to validate or invalidate decisions.
3. User preferences always carry extra weight.
4. Write a clear 1-sentence "context" for EACH decision explaining your reasoning.
5. decisions MUST appear in your output — only skip if both context and status are already there and there is no need to update them.

### OUTPUT FORMAT (Strict JSON Array)
[
  { "id": 12, "status": "superseded", "context": "Replaced by Decision #45 which switched to raw SQL." },
  { "id": 45, "status": "active", "context": "Currently active. Aligns with user preference for explicit control." }
]
"""

# Time window: only fetch supporting memories within ±T hours of the decision range
SUPPORT_WINDOW_MINS = 5

class DecisionStateAnalyzer:
    def __init__(self):
        self.extractor = Summary_Extractor(sys_prompt=ANALYSIS_PROMPT)

    def analyze(self, session: Session, topic_id: int, user_id: str = "default"):
        """
        Analyze all decisions for a given topic and update their statuses.
        Called after new DecisionMemory rows are inserted for this topic.
        """
        # 1. Fetch all decisions for this topic and user
        decisions = (
            session.query(DecisionMemory)
            .filter(
                DecisionMemory.topic_id == topic_id,
                DecisionMemory.user_id == user_id
            )
            .order_by(DecisionMemory.timestamp.desc())
            .all()
        )

        if not decisions:
            return

        # 2. Compute time window from only NEW decisions (no context yet)
        new_decisions = [d for d in decisions if not d.context]
        new_timestamps = [d.timestamp for d in new_decisions if d.timestamp]
        if new_timestamps:
            earliest = min(new_timestamps) - timedelta(minutes=SUPPORT_WINDOW_MINS)
            latest = max(new_timestamps) + timedelta(minutes=SUPPORT_WINDOW_MINS)
        else:
            return

        # 3. Fetch supporting context within the time window filtered by user
        episodic = session.query(EpisodicMemory).filter(
            EpisodicMemory.topic_id == topic_id,
            EpisodicMemory.user_id == user_id,
            EpisodicMemory.timestamp >= earliest,
            EpisodicMemory.timestamp <= latest
        ).order_by(EpisodicMemory.timestamp.desc()).all()

        knowledge = session.query(KnowledgeMemory).filter(
            KnowledgeMemory.topic_id == topic_id,
            KnowledgeMemory.user_id == user_id,
            KnowledgeMemory.timestamp >= earliest,
            KnowledgeMemory.timestamp <= latest
        ).order_by(KnowledgeMemory.timestamp.desc()).all()

        user_mems = session.query(UserMemory).filter(
            UserMemory.topic_id == topic_id,
            UserMemory.user_id == user_id,
            UserMemory.timestamp >= earliest,
            UserMemory.timestamp <= latest
        ).order_by(UserMemory.timestamp.desc()).all()

        # 4. Build prompt with timestamps
        decisions_text = "\n".join([
            f"ID {d.id} [{d.timestamp.strftime('%Y-%m-%d %H:%M') if d.timestamp else '?'}]: \"{d.content}\" [status: {d.status}] [context: {d.context or 'None'}]"
            for d in decisions
        ])

        support_lines = []
        for m in episodic:
            ts = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else '?'
            support_lines.append(f"[EPISODIC {ts}] {m.content}")
        for m in knowledge:
            ts = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else '?'
            support_lines.append(f"[KNOWLEDGE {ts}] {m.content}")
        for m in user_mems:
            ts = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else '?'
            support_lines.append(f"[USER_PREF {ts}] {m.content}")

        support_text = "\n".join(support_lines) if support_lines else "(No supporting memories)"

        user_prompt = f"""Decisions for this topic:
            {decisions_text}

            Supporting Context:
            {support_text}
            
            Analyze and output the status for each decision as JSON."""

        raw_response = self.extractor.summary_extract(user_prompt)

        if not raw_response:
            logger.info(f"[DecisionAnalyzer] LLM returned empty for topic {topic_id}")
            return

        try:
            if isinstance(raw_response, str):   
                s = raw_response.find("[")
                e = raw_response.rfind("]")
                if s != -1 and e != -1:
                    raw_response = raw_response[s:e+1]
                updates = json.loads(raw_response)
            else:
                updates = raw_response

            if not isinstance(updates, list):
                logger.info(f"[DecisionAnalyzer] Expected list, got {type(updates)}")
                return

        except Exception as e:
            logger.error(f"[DecisionAnalyzer] Parse error: {e}")
            return

        decisions_by_id = {d.id: d for d in decisions}

        for update in updates:
            did = update.get("id")
            new_status = update.get("status")
            new_context = update.get("context")

            if did not in decisions_by_id:
                continue

            decision = decisions_by_id[did]

            valid_statuses = {"active", "inactive", "superseded", "rejected", "conditional"}
            if new_status in valid_statuses:
                decision.status = new_status

            if new_context:
                decision.context = new_context

            decision.last_validated_at = datetime.now()
            session.add(decision)

        logger.info(f"[DecisionAnalyzer] Updated {len(updates)} decisions for topic {topic_id}")