import os
import re
import time
import threading
import requests
import logging
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
from mindcache.exceptions import ProviderError

class AllKeysExhaustedError(ProviderError):
    pass

class SafeAI:
    # ── Global state shared across ALL instances / threads / workers ──
    _exhausted_keys: set = set()       # keys confirmed quota-dead this session
    _active_keys: set = set()          # keys currently in use by any active request
    _key_lock: threading.Lock = threading.Lock()
    _keys_loaded: list | None = None   # loaded once, shared by all instances

    def __init__(self, model_name=DEFAULT_MODEL, provider="gemini"):
        # Load keys only once into the class-level list if using Gemini REST
        if SafeAI._keys_loaded is None and provider == "gemini":
            try:
                SafeAI._keys_loaded = self._load_keys()
            except Exception as e:
                # Fail gracefully if keys aren't found yet (e.g. user will supply them later)
                SafeAI._keys_loaded = []

        self.model_name = model_name
        self.provider = provider.lower()

    @property
    def keys_loaded(self):
        if SafeAI._keys_loaded is None:
            return []
        return SafeAI._keys_loaded

    @staticmethod
    def _load_keys() -> list:
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "keys.text")
        if not os.path.exists(config_path):
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "keys.txt")
        if not os.path.exists(config_path):
            config_path = "config/keys.text" if os.path.exists("config/keys.text") else "config/keys.txt"

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]

        keys_env = os.environ.get("GEMINI_API_KEYS")
        if keys_env:
            return [k.strip() for k in keys_env.split(",") if k.strip()]

        single_key = os.environ.get("GEMINI_API_KEY")
        if not single_key:
            raise EnvironmentError(
                "API keys file (config/keys.text) and environment variables "
                "GEMINI_API_KEYS / GEMINI_API_KEY not found."
            )
        return [single_key.strip()]

    @classmethod
    def _acquire_key(cls, preferred_idx: int) -> int:
        keys = cls._keys_loaded
        num_keys = len(keys)
        if num_keys == 0:
            raise AllKeysExhaustedError("No API keys loaded.")
        while True:
            with cls._key_lock:
                if len(cls._exhausted_keys) >= num_keys:
                    logger.warning("[SafeAI] All keys temporarily rate-limited. Sleeping 15s before resetting key pool...")
                    time.sleep(15)
                    cls._exhausted_keys.clear()
                
                for offset in range(num_keys):
                    idx = (preferred_idx + offset) % num_keys
                    if idx not in cls._exhausted_keys and idx not in cls._active_keys:
                        cls._active_keys.add(idx)
                        key = keys[idx]
                        logger.info(f"[SafeAI] Using Key {idx + 1}/{num_keys} ending in ...{key[-4:] if len(key) > 4 else ''}")
                        return idx
            time.sleep(1)

    @classmethod
    def _release_key(cls, idx: int):
        with cls._key_lock:
            cls._active_keys.discard(idx)

    @classmethod
    def _exhaust_key(cls, idx: int):
        with cls._key_lock:
            cls._exhausted_keys.add(idx)
            cls._active_keys.discard(idx)
            logger.info(f"[SafeAI] Key {idx + 1} marked as EXHAUSTED.")

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 128000,
        json_schema: dict | None = None,
        retries: int = 50
    ):
        # ── Path A: LiteLLM Universal Provider ─────────────────────────────
        if self.provider != "gemini":
            try:
                import litellm
            except ImportError:
                raise ImportError("litellm is required for non-gemini providers. Run 'pip install litellm'.")

            # Route call via LiteLLM completion
            for attempt in range(retries):
                try:
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})

                    completion_kwargs = {
                        "model": self.model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }

                    if json_schema:
                        # Map Pydantic/JSON schema for OpenAI/Anthropic structured outputs
                        completion_kwargs["response_format"] = {
                            "type": "json_object",
                            "schema": self._clean_schema(json_schema)
                        }

                    response = litellm.completion(**completion_kwargs)
                    raw_text = response.choices[0].message.content
                    if raw_text:
                        raw_text = self._sanitize_output(raw_text)
                    return self.clean_json(raw_text) if raw_text else None
                except Exception as e:
                    logger.warning(f"[LiteLLM Provider Warning] Attempt {attempt+1} failed: {e}")
                    time.sleep(2)
            return None

        # ── Path B: Original Gemini Multi-Key REST Rotation ─────────────────
        with SafeAI._key_lock:
            if not hasattr(SafeAI, "_preferred_start_idx"):
                SafeAI._preferred_start_idx = 0
            preferred_idx = SafeAI._preferred_start_idx
            # Guard against division by zero if keys aren't loaded yet
            num_keys = len(SafeAI._keys_loaded) if SafeAI._keys_loaded else 0
            if num_keys > 0:
                SafeAI._preferred_start_idx = (SafeAI._preferred_start_idx + 1) % num_keys

        try:
            current_idx = SafeAI._acquire_key(preferred_idx)
        except AllKeysExhaustedError:
            raise AllKeysExhaustedError("All provided API keys trigger quota/exhaustion errors.")

        try:
            for attempt in range(retries):
                try:
                    url = f"{GEMINI_API_BASE}/{self.model_name}:generateContent"
                    key = SafeAI._keys_loaded[current_idx]
                    params = {"key": key}

                    # Build request body
                    payload = {
                        "contents": [
                            {"role": "user", "parts": [{"text": prompt}]}
                        ],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens,
                        }
                    }

                    if system_prompt:
                        payload["systemInstruction"] = {
                            "parts": [{"text": system_prompt}]
                        }

                    if json_schema:
                        payload["generationConfig"]["responseMimeType"] = "application/json"
                        payload["generationConfig"]["responseSchema"] = self._clean_schema(json_schema)

                    resp = requests.post(url, params=params, json=payload, timeout=300)

                    # Handle HTTP-level errors
                    if resp.status_code in (429, 403, 401):
                        raise _QuotaError(f"{resp.status_code} {resp.text}")
                    if resp.status_code == 503:
                        raise _OverloadError(f"503 {resp.text}")
                    if resp.status_code != 200:
                        raise Exception(f"{resp.status_code} {resp.text}")

                    data = resp.json()

                    # Check finish reason
                    candidates = data.get("candidates", [])
                    if candidates:
                        finish_reason = candidates[0].get("finishReason", "").upper()
                        if "MAX_TOKENS" in finish_reason or "LENGTH" in finish_reason:
                            logger.info(f"[SafeAI] Output truncated ({finish_reason}). Attempting to salvage partial JSON...")
                            usage = data.get("usageMetadata", {})
                            logger.info(f"Prompt tokens: {usage.get('promptTokenCount')}")
                            logger.info(f"Output tokens: {usage.get('candidatesTokenCount')}")
                            return None

                    raw_text = self._extract_text(data)
                    if raw_text:
                        raw_text = self._sanitize_output(raw_text)
                    return self.clean_json(raw_text) if raw_text else None

                except _OverloadError:
                    time.sleep(10)
                    continue

                except _QuotaError:
                    SafeAI._exhaust_key(current_idx)
                    try:
                        current_idx = SafeAI._acquire_key(current_idx + 1)
                    except AllKeysExhaustedError:
                        raise AllKeysExhaustedError("All provided API keys trigger quota/exhaustion errors.")
                    continue

                except Exception as e:
                    error_msg = str(e).lower()

                    # Catch 503 High Demand / Global Overload
                    if "503" in error_msg or "demand" in error_msg or "spike" in error_msg:
                        time.sleep(10)
                        continue

                    # Catch 429 / 403 / quota errors
                    if any(x in error_msg for x in ["429", "403", "exhausted", "quota"]):
                        SafeAI._exhaust_key(current_idx)
                        try:
                            current_idx = SafeAI._acquire_key(current_idx + 1)
                        except AllKeysExhaustedError:
                            raise AllKeysExhaustedError("All provided API keys trigger quota/exhaustion errors.")
                        continue

                    # Catch timeouts / connection errors / network issues -> switch key immediately!
                    if any(x in error_msg for x in ["timeout", "timed out", "connection", "pool"]):
                        logger.info(f"[SafeAI] Network issue/timeout on Key {current_idx + 1}: {e}. Switching key...")
                        old_idx = current_idx
                        try:
                            current_idx = SafeAI._acquire_key(old_idx + 1)
                        except AllKeysExhaustedError:
                            raise AllKeysExhaustedError("All provided API keys trigger quota/exhaustion errors.")
                        finally:
                            SafeAI._release_key(old_idx)
                        continue

                    if attempt == retries - 1:
                        logger.info(f"[SafeAI] Failed after retries: {e}")
                        return None
        finally:
            SafeAI._release_key(current_idx)

    @staticmethod
    def _extract_text(data: dict):
        """Extract text from raw REST API JSON response."""
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            texts = [p["text"] for p in parts if "text" in p]
            return "".join(texts) if texts else None
        except Exception:
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
    def clean_json(text):
        text = re.sub(r"```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```", "", text)
        return text.strip()

    @staticmethod
    def _sanitize_output(text):
        """Strip degenerate repetition loops from LLM JSON output.
        Gemini enters token loops writing \n\n\n... or \t\t\t... inside string values.
        This strips all three patterns: pure newlines, pure tabs, and mixed."""
        text = re.sub(r'\n{3,}', ' ', text)       # \n\n\n... loop → space
        text = re.sub(r'\n\t{2,}', ' ', text)     # \n + tabs loop → space
        text = re.sub(r'\t{3,}', ' ', text)        # pure tabs loop → space
        return text


# Internal sentinel exceptions for cleaner control flow
class _QuotaError(Exception):
    pass

class _OverloadError(Exception):
    pass