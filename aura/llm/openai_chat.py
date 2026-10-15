"""ChatModel adapter for the OpenAI Chat Completions API."""

import json
import math
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def llm_from_environment(
    *, model_setting: str = "OPENAI_MODEL"
) -> Optional["OpenAIChatModel"]:
    """Select real models explicitly; retain validated legacy provider settings."""
    _load_env()
    mode = os.environ.get("AURA_MODE", "").strip().lower()
    provider = os.environ.get("AURA_LLM", "").strip().lower()
    if mode not in {"", "demo", "live"}:
        raise ValueError("AURA_MODE must be demo or live")
    if provider not in {"", "none", "openai"}:
        raise ValueError("AURA_LLM must be none or openai")
    if mode == "demo":
        return None
    if mode == "live":
        if provider == "none":
            raise ValueError("Live mode requires AURA_LLM=openai, not none")
        return OpenAIChatModel(model=os.environ.get(model_setting, "").strip() or None)
    if provider == "openai":
        return OpenAIChatModel(model=os.environ.get(model_setting, "").strip() or None)
    return None


class OpenAIChatModel:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY is not configured in environment or .env")
        self._model = (model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)).strip()
        self._base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)
        ).strip().rstrip("/")
        parsed_url = urlparse(self._base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("OPENAI_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment")
        if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote OPENAI_BASE_URL must use HTTPS")
        if not self._model:
            raise ValueError("OPENAI_MODEL must be non-empty")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("LLM timeout must be a finite positive number")
        self._timeout = timeout

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        try:
            response = requests.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.Timeout:
            raise RuntimeError("LLM request timed out; no model result is available") from None
        except requests.RequestException:
            raise RuntimeError("LLM request failed; check provider configuration and availability") from None

        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
            if choice.get("finish_reason") not in {None, "stop"} or message.get("refusal"):
                raise ValueError("Incomplete or refused model output")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty model output")
            if not isinstance(json.loads(content), dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("LLM returned an invalid, incomplete, or refused JSON response") from None
        return content
