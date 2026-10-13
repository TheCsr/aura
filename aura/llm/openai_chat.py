"""ChatModel adapter for the OpenAI Chat Completions API."""

import os
from pathlib import Path
from typing import Optional

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


def llm_from_environment() -> Optional["OpenAIChatModel"]:
    """Return an OpenAI model when AURA_LLM=openai, otherwise None (use fakes)."""
    _load_env()
    if os.environ.get("AURA_LLM", "").strip().lower() == "openai":
        return OpenAIChatModel()
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
        self._timeout = timeout

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
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
        return response.json()["choices"][0]["message"]["content"] or ""
