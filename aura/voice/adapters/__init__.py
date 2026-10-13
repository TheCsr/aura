"""Presentation and device adapters for voice I/O."""

from .fake_stt import FakeSpeechToTextAdapter
from .google_tts import GoogleTranslateTtsAdapter
from .orange_kb_whisper import OrangeKbWhisperAdapter
from .wav_signal_analyzer import AudioSignalAnalysisError, PcmWavSignalAnalyzer

__all__ = [
	"AudioSignalAnalysisError",
	"FakeSpeechToTextAdapter",
	"GoogleTranslateTtsAdapter",
	"OrangeKbWhisperAdapter",
	"PcmWavSignalAnalyzer",
]