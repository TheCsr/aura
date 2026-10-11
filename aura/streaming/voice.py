"""Scripted voice interaction for a deterministic streaming demo."""

from io import BytesIO
import math
import struct
import wave
from typing import Optional, Protocol, Tuple

from ..agents.supervisor import Assessment
from ..contracts import InputEvent
from ..voice.contracts import AudioRecording, CorrelatedVoiceReply, VoicePrompt
from ..voice.ports import Microphone, Speaker, VocalSignalAnalyzer, VoiceTranscriber
from ..voice.session import ResidentVoiceSession


class VoiceResponder(Protocol):
    def ask_and_capture(
        self,
        event: InputEvent,
        assessment: Assessment,
    ) -> Tuple[VoicePrompt, CorrelatedVoiceReply]:
        """Speak one follow-up prompt and return its correlated resident reply."""


class SensorAnomalyPromptBuilder:
    def build(self, event: InputEvent, assessment: Assessment) -> str:
        return (
            "Hej. Vi har märkt en avvikelse i sensorerna hemma hos dig. "
            "Är allt bra? Berätta gärna om du behöver hjälp."
        )


class ScriptedVoiceResponder:
    """Use injected speaker, microphone, STT, and analyzer ports for one reply."""

    def __init__(
        self,
        speaker: Speaker,
        microphone: Microphone,
        transcriber: VoiceTranscriber,
        signal_analyzer: VocalSignalAnalyzer,
        *,
        prompt_builder: Optional[SensorAnomalyPromptBuilder] = None,
    ) -> None:
        self._speaker = speaker
        self._microphone = microphone
        self._transcriber = transcriber
        self._signal_analyzer = signal_analyzer
        self._prompt_builder = prompt_builder or SensorAnomalyPromptBuilder()

    def ask_and_capture(
        self,
        event: InputEvent,
        assessment: Assessment,
    ) -> Tuple[VoicePrompt, CorrelatedVoiceReply]:
        voice_session = ResidentVoiceSession(
            self._speaker,
            self._transcriber,
            signal_analyzer=self._signal_analyzer,
        )
        prompt = voice_session.ask(
            event.event_id,
            self._prompt_builder.build(event, assessment),
        )
        recording = self._microphone.capture()
        if recording is None:
            raise RuntimeError("No scripted resident response is available")
        reply = voice_session.process_reply(prompt.conversation_id, recording, language="sv")
        return prompt, reply


class SyntheticSignalMicrophone:
    """Create a short synthetic test tone, not a human speech recording."""

    def __init__(self, *, sample_rate_hz: int = 16000, duration_seconds: float = 0.4) -> None:
        if (
            isinstance(sample_rate_hz, bool)
            or not isinstance(sample_rate_hz, int)
            or sample_rate_hz < 8000
        ):
            raise ValueError("sample_rate_hz must be an integer of at least 8000")
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive finite number")
        self._sample_rate_hz = sample_rate_hz
        self._duration_seconds = duration_seconds
        self.capture_count = 0

    def capture(self) -> AudioRecording:
        self.capture_count += 1
        frame_count = int(self._sample_rate_hz * self._duration_seconds)
        amplitude = 4500
        samples = [
            int(amplitude * math.sin(2.0 * math.pi * 440.0 * frame / self._sample_rate_hz))
            for frame in range(frame_count)
        ]
        audio_buffer = BytesIO()
        with wave.open(audio_buffer, "wb") as wave_file:
            wave_file.setnchannels(1)
            wave_file.setsampwidth(2)
            wave_file.setframerate(self._sample_rate_hz)
            wave_file.writeframes(struct.pack("<" + "h" * frame_count, *samples))
        return AudioRecording(
            audio=audio_buffer.getvalue(),
            filename="synthetic-resident-response.wav",
            content_type="audio/wav",
        )