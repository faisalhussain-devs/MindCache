from Memory_extract.safe_ai import SafeAI

class Summary_Extractor():
    def __init__(self, sys_prompt=None):
        self.sys_prompt = sys_prompt

    def summary_extract(self, prompt):
        self.llm = SafeAI()
        raw_json = self.llm.generate(
            prompt=prompt,
            system_prompt=self.sys_prompt
        )
        if not raw_json:
            return None
        try:
            return raw_json
        except Exception as e:
            print(f"[SafeAI] Validation Failed for summary extractor: {e}")
            return None