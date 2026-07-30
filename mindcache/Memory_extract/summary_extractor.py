from mindcache.Memory_extract.safe_ai import SafeAI
import logging
logger = logging.getLogger(__name__)

class Summary_Extractor():
    def __init__(self, sys_prompt=None):
        self.sys_prompt = sys_prompt

    def summary_extract(self, prompt):
        llm = SafeAI()
        raw_json = llm.generate(
            prompt=prompt,
            system_prompt=self.sys_prompt,
            retries=100
        )
        if not raw_json:
            return None
        try:
            return raw_json
        except Exception as e:
            logger.info(f"[SafeAI] Validation Failed for summary extractor: {e}")
            return None