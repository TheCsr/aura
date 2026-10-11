"""Replaceable hardware and browser I/O interfaces."""

from typing import Optional, Protocol

from ..contracts import Transcript
from .contracts import AudioRecording, SpeakerReceipt, VocalSignalAnalysis


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakerReceipt:
        """Output a prompt through a physical or simulated speaker."""


class Microphone(Protocol):
    def capture(self) -> Optional[AudioRecording]:
        """Return one captured recording, or None while awaiting user input."""


class VoiceTranscriber(Protocol):
    def transcribe(self, recording: AudioRecording, *, language: str) -> Transcript:
        """Convert captured audio into text through a provider or a test double."""


class VocalSignalAnalyzer(Protocol):
    def analyze(
        self,
        recording: AudioRecording,
        *,
        event_id: str,
        conversation_id: str,
    ) -> VocalSignalAnalysis:
        """Extract acoustic measurements without inferring a diagnosis."""