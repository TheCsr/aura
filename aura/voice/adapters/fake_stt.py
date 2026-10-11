"""Scripted STT adapter for local demos and deterministic tests."""

from typing import List, Tuple

from ...contracts import Transcript
from ..contracts import AudioRecording


class FakeSpeechToTextAdapter:
    """Return configured transcript text; audio is validated but not analyzed."""

    def __init__(self, transcript_text: str = "") -> None:
        if not isinstance(transcript_text, str):
            raise ValueError("transcript_text must be a string")
        self.transcript_text = transcript_text
        self.calls: List[Tuple[str, str, int]] = []

    def set_transcript(self, transcript_text: str) -> None:
        if not isinstance(transcript_text, str):
            raise ValueError("transcript_text must be a string")
        self.transcript_text = transcript_text

    def transcribe(self, recording: AudioRecording, *, language: str) -> Transcript:
        if not isinstance(recording, AudioRecording):
            raise ValueError("recording must be AudioRecording")
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a non-empty string")
        self.calls.append((recording.filename, language, len(recording.audio)))
        if not self.transcript_text.strip():
            raise ValueError("Set a fake transcript before transcribing")
        return Transcript(
            text=self.transcript_text.strip(),
            source="fake-stt",
            language=language,
            model="scripted-fake",
        )