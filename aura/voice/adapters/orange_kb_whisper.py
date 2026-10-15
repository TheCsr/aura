"""KB-Whisper Speech-to-Text adapter using the Orange cloud endpoint."""

import logging
import os
from typing import Optional
import requests

from ...contracts import Transcript
from ..contracts import AudioRecording

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://kb-whisper-vdr-tieto-poc-fa09560c.caas.mandarine-business.cloud"
DEFAULT_MODEL = "KBLab/kb-whisper-large"


class OrangeKbWhisperAdapter:
    """Transcribe Swedish audio recordings using the KB-Whisper API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: tuple[int, int] = (15, 60),
    ) -> None:
        self._api_key = (api_key or os.environ.get("ORANGE_API_KEY", "")).strip()
        configured_url = os.environ.get("KB_WHISPER_URL") or os.environ.get("BSER_URL") or DEFAULT_BASE_URL
        self._base_url = (base_url or configured_url).strip().rstrip("/")
        self._model = (model or os.environ.get("KB_WHISPER_MODEL", DEFAULT_MODEL)).strip()
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def transcribe(self, recording: AudioRecording, *, language: str = "sv") -> Transcript:
        if not isinstance(recording, AudioRecording):
            raise ValueError("recording must be an AudioRecording")
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a non-empty string")

        api_key = self._api_key or os.environ.get("ORANGE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ORANGE_API_KEY is not configured in environment or .env. "
                "Please configure ORANGE_API_KEY to use KB-Whisper Swedish transcription."
            )

        endpoint = f"{self._base_url}/v1/audio/transcriptions"
        filename = recording.filename or "recording.wav"
        content_type = recording.content_type or "audio/wav"

        files = {"file": (filename, recording.audio, content_type)}
        data = {
            "model": self._model,
            "language": language,
            "response_format": "json",
        }

        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
                data=data,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            text = payload.get("text", "") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Transcription API response did not contain a valid 'text' string")

            return Transcript(
                text=text.strip(),
                source="orange-kb-whisper",
                language=language,
                model=self._model,
            )
        except requests.RequestException:
            raise RuntimeError("KB-Whisper request failed; please retry the recording") from None
