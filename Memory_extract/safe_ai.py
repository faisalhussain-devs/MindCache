import os
import re
from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-2.5-flash"

class SafeAI:
    def __init__(self, model_name=DEFAULT_MODEL):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")

        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        json_mode: bool = False,
        retries: int = 1,
    ):
        for attempt in range(retries):
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )

                if system_prompt:
                    config.system_instruction = system_prompt

                if json_mode:
                    config.response_mime_type = "application/json"

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )

                raw_text = self._extract_text(response)
                return self.clean_json(raw_text) if raw_text else None

            except Exception as e:
                if attempt == retries - 1:
                    print(f"[SafeAI] Failed after retries: {e}")
                    return None

    @staticmethod
    def _extract_text(response):
        if hasattr(response, "text") and response.text:
            return response.text
        if response.candidates:
            return response.candidates[0].content.parts[0].text
        return None

    @staticmethod
    def clean_json(text):
        text = re.sub(r"```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```", "", text)
        return text.strip()
