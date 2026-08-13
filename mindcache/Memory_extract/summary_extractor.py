from mindcache.Memory_extract.safe_ai import SafeAI
import logging
logger = logging.getLogger(__name__)

class Summary_Extractor():
    def __init__(
        self,
        sys_prompt=None,
        model_name="gemini-2.5-flash",
        provider="gemini",
    ):
        self.sys_prompt = sys_prompt
        self.model_name = model_name
        self.provider = provider

    def summary_extract(self, prompt):
        llm = SafeAI(
            model_name=self.model_name,
            provider=self.provider,
        )
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