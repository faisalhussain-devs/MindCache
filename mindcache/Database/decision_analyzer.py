import json
from datetime import datetime
from sqlalchemy.orm import Session
from mindcache.Database.db_setup import DecisionMemory
from mindcache.Memory_extract.summary_extractor import Summary_Extractor
import logging
logger = logging.getLogger(__name__)


ANALYSIS_PROMPT = """You are the Decision State Analyzer for a memory database.
You will receive a cluster of semantically related decisions.

Your job: Determine the current STATUS of each decision within this semantic cluster.

### STATUS OPTIONS
- "active"      → This decision is currently in effect.
- "superseded"  → A newer decision in this cluster replaced/evolved this one.
- "rejected"    → This decision was wrong or abandoned in favor of another one in this cluster.
- "conditional" → This decision only applies under specific conditions.
- "inactive"    → No longer relevant but not explicitly rejected.

### RULES
1. If two decisions contradict each other, the NEWER one (higher ID or higher timestamp) is usually "active" and the older one is "superseded".
2. Compare the decisions carefully to see if a newer one overrides, details, or discards an older one.
3. Write a clear 1-sentence "context" for EACH decision explaining your reasoning.
4. All decisions in the cluster MUST appear in your output — only skip if both context and status are already there and there is no need to update them.

### OUTPUT FORMAT (Strict JSON Array)
[
  { "id": 12, "status": "superseded", "context": "Replaced by Decision #45 which switched to raw SQL." },
  { "id": 45, "status": "active", "context": "Currently active version of the database choice decision." }
]
"""


class DecisionStateAnalyzer:
    def __init__(
        self,
        model_name="gemini-2.5-flash",
        provider="gemini",
    ):
        self.extractor = Summary_Extractor(
            sys_prompt=ANALYSIS_PROMPT,
            model_name=model_name,
            provider=provider,
        )

    def analyze_cluster(self, session: Session, cluster_decisions: list[DecisionMemory]):
        """
        Analyze a cluster of semantically related decisions and update their statuses.
        """
        if not cluster_decisions:
            return

        # Sort oldest first (higher ID means newer)
        sorted_decisions = sorted(cluster_decisions, key=lambda d: d.id)

        # Build prompt with timestamps and contents
        decisions_text = "\n".join([
            f"ID {d.id} [{d.timestamp.strftime('%Y-%m-%d %H:%M') if d.timestamp else '?'}]: \"{d.content}\" [status: {d.status}] [context: {d.context or 'None'}]"
            for d in sorted_decisions
        ])

        user_prompt = f"""Semantically related decisions in this cluster:
            {decisions_text}
            
            Analyze and output the status for each decision as JSON."""

        raw_response = self.extractor.summary_extract(user_prompt)

        if not raw_response:
            logger.info(f"[DecisionAnalyzer] LLM returned empty for cluster {[d.id for d in cluster_decisions]}")
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

        decisions_by_id = {d.id: d for d in cluster_decisions}

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

        logger.info(f"[DecisionAnalyzer] Updated {len(updates)} decisions in cluster")