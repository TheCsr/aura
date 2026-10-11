"""Mock voice components for offline development and presentation rehearsals."""

from typing import Optional, Tuple

from .contracts import AudioRecording, SpeakerReceipt
from .adapters.fake_stt import FakeSpeechToTextAdapter as MockVoiceTranscriber


class MockSpeaker:
    def __init__(self) -> None:
        self._receipts = []

    @property
    def receipts(self) -> Tuple[SpeakerReceipt, ...]:
        return tuple(self._receipts)

    def speak(self, text: str) -> SpeakerReceipt:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("speaker text must be a non-empty string")
        receipt = SpeakerReceipt(
            text=text.strip(),
            output_mode="mock_text_preview",
            delivered=False,
        )
        self._receipts.append(receipt)
        return receipt


class MockMicrophone:
    def __init__(self, recording: Optional[AudioRecording] = None) -> None:
        self._recording = recording
        self.capture_count = 0

    def set_recording(self, recording: Optional[AudioRecording]) -> None:
        self._recording = recording

    def capture(self) -> Optional[AudioRecording]:
        self.capture_count += 1
        return self._recording

