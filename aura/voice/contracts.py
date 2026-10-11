"""Domain contracts for speaker output and captured microphone audio."""

from dataclasses import dataclass
import math
from typing import Optional

from ..contracts import Transcript


@dataclass(frozen=True)
class SpeakerReceipt:
    text: str
    output_mode: str
    delivered: bool

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("speaker text must be a non-empty string")
        if not isinstance(self.output_mode, str) or not self.output_mode.strip():
            raise ValueError("output_mode must be a non-empty string")
        if not isinstance(self.delivered, bool):
            raise ValueError("delivered must be a boolean")


@dataclass(frozen=True)
class AudioRecording:
    audio: bytes
    filename: str
    content_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.audio, bytes) or not self.audio:
            raise ValueError("audio must contain captured bytes")
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("filename must be a non-empty string")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValueError("content_type must be a non-empty string")


@dataclass(frozen=True)
class VoicePrompt:
    conversation_id: str
    event_id: str
    text: str
    speaker_receipt: SpeakerReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("prompt text must be a non-empty string")


@dataclass(frozen=True)
class CorrelatedTranscript:
    conversation_id: str
    event_id: str
    transcript: Transcript

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.transcript, Transcript):
            raise ValueError("transcript must be a Transcript")


@dataclass(frozen=True)
class VocalSignalAnalysis:
    event_id: str
    conversation_id: str
    source: str
    features: dict[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.features, dict) or not self.features:
            raise ValueError("features must be a non-empty mapping")
        for name, value in self.features.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("feature names must be non-empty strings")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"feature {name!r} must be a finite number")


@dataclass(frozen=True)
class CorrelatedVoiceReply:
    event_id: str
    conversation_id: str
    transcript: Transcript
    signal_analysis: VocalSignalAnalysis

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(self.transcript, Transcript):
            raise ValueError("transcript must be a Transcript")
        if not isinstance(self.signal_analysis, VocalSignalAnalysis):
            raise ValueError("signal_analysis must be a VocalSignalAnalysis")
        if (
            self.signal_analysis.event_id != self.event_id
            or self.signal_analysis.conversation_id != self.conversation_id
        ):
            raise ValueError("signal analysis must match the reply event and conversation")