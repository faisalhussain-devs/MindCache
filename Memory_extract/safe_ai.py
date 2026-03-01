import os
import re
from google import genai
from google.genai import types
import time

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
        max_tokens: int = 16384,
        json_schema: dict | None = None,
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

                if json_schema:
                    config.response_mime_type = "application/json"
                    config.response_schema = self._clean_schema(json_schema)

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )

                time.sleep(8)
                raw_text = self._extract_text(response)
                return self.clean_json(raw_text) if raw_text else None

            except Exception as e:
                if attempt == retries - 1:
                    print(f"[SafeAI] Failed after retries: {e}")
                    return None

    @staticmethod
    def _clean_schema(schema):
        """
        Make Pydantic JSON schema compatible with Gemini API.
        - Resolves $ref pointers by inlining definitions from $defs
        - Strips additionalProperties, $defs, title (unsupported by Gemini)
        """
        defs = schema.get("$defs", {})

        def resolve(node):
            if isinstance(node, dict):
                # Replace $ref with the actual definition
                if "$ref" in node:
                    ref_path = node["$ref"]  # e.g. "#/$defs/ThinkingStep"
                    ref_name = ref_path.split("/")[-1]
                    if ref_name in defs:
                        return resolve(defs[ref_name])  # Recursively resolve
                    return node

                return {
                    k: resolve(v) for k, v in node.items()
                    if k not in ("additionalProperties", "$defs", "title")
                }
            elif isinstance(node, list):
                return [resolve(item) for item in node]
            return node

        return resolve(schema)

    @staticmethod
    def _extract_text(response):
        try:
            if hasattr(response, "text") and response.text:
                return response.text
        except Exception:
            pass
        
        if response.candidates:
            text_parts = []
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            if text_parts:
                return "".join(text_parts)
        return None

    @staticmethod
    def clean_json(text):
        text = re.sub(r"```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```", "", text)
        return text.strip()
