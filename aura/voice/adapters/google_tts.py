"""Google Translate Text-to-Speech (TTS) adapter using gTTS."""

from io import BytesIO
import logging
from typing import Optional
from gtts import gTTS

logger = logging.getLogger(__name__)


class GoogleTranslateTtsAdapter:
    """Synthesize Swedish audio speech from text using Google Translate TTS."""

    def __init__(self, default_language: str = "sv") -> None:
        self.default_language = default_language

    def synthesize(self, text: str, *, language: Optional[str] = None) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        lang = language or self.default_language

        buffer = BytesIO()
        tts = gTTS(text=text.strip(), lang=lang, slow=False)
        tts.write_to_fp(buffer)
        buffer.seek(0)
        return buffer.getvalue()
