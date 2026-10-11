"""Bounded acoustic measurements from uncompressed 16-bit PCM WAV audio."""

from io import BytesIO
import math
import struct
import wave

from ..contracts import AudioRecording, VocalSignalAnalysis


class AudioSignalAnalysisError(ValueError):
    """Raised when a recording cannot be analyzed as supported PCM WAV audio."""


class PcmWavSignalAnalyzer:
    """Measure waveform statistics only; these are not pain or health biomarkers."""

    def __init__(
        self,
        *,
        max_audio_bytes: int = 8 * 1024 * 1024,
        max_duration_seconds: float = 120.0,
        silence_rms_threshold: float = 0.01,
    ) -> None:
        if isinstance(max_audio_bytes, bool) or not isinstance(max_audio_bytes, int) or max_audio_bytes < 1:
            raise ValueError("max_audio_bytes must be a positive integer")
        if (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not math.isfinite(max_duration_seconds)
            or max_duration_seconds <= 0
        ):
            raise ValueError("max_duration_seconds must be a positive finite number")
        if (
            isinstance(silence_rms_threshold, bool)
            or not isinstance(silence_rms_threshold, (int, float))
            or not math.isfinite(silence_rms_threshold)
            or not 0.0 <= silence_rms_threshold <= 1.0
        ):
            raise ValueError("silence_rms_threshold must be between 0 and 1")
        self._max_audio_bytes = max_audio_bytes
        self._max_duration_seconds = float(max_duration_seconds)
        self._silence_rms_threshold = float(silence_rms_threshold)

    def analyze(
        self,
        recording: AudioRecording,
        *,
        event_id: str,
        conversation_id: str,
    ) -> VocalSignalAnalysis:
        if not isinstance(recording, AudioRecording):
            raise AudioSignalAnalysisError("recording must be AudioRecording")
        if not isinstance(event_id, str) or not event_id.strip():
            raise AudioSignalAnalysisError("event_id must be a non-empty string")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise AudioSignalAnalysisError("conversation_id must be a non-empty string")
        if len(recording.audio) > self._max_audio_bytes:
            raise AudioSignalAnalysisError("recording exceeds max_audio_bytes")

        try:
            with wave.open(BytesIO(recording.audio), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                compression = wav_file.getcomptype()
                raw_samples = wav_file.readframes(frame_count)
        except (wave.Error, EOFError, OSError) as error:
            raise AudioSignalAnalysisError("recording must be a readable WAV file") from error

        if compression != "NONE" or sample_width != 2:
            raise AudioSignalAnalysisError("only uncompressed 16-bit PCM WAV is supported")
        if channels not in (1, 2):
            raise AudioSignalAnalysisError("WAV audio must have one or two channels")
        if sample_rate <= 0 or frame_count <= 0:
            raise AudioSignalAnalysisError("WAV audio must contain frames at a valid sample rate")
        if frame_count / sample_rate > self._max_duration_seconds:
            raise AudioSignalAnalysisError("recording exceeds max_duration_seconds")
        expected_bytes = frame_count * channels * sample_width
        if len(raw_samples) != expected_bytes:
            raise AudioSignalAnalysisError("WAV sample data is incomplete")

        square_sum = 0.0
        peak_amplitude = 0.0
        zero_crossings = 0
        previous_sample = None
        silence_windows = 0
        total_windows = 0
        window_samples = max(1, sample_rate // 50)
        window_square_sum = 0.0
        samples_in_window = 0

        sample_iterator = struct.iter_unpack("<h", raw_samples)
        for _ in range(frame_count):
            frame_sum = 0.0
            for _ in range(channels):
                integer_sample = next(sample_iterator)[0]
                frame_sum += integer_sample / 32768.0
            sample = frame_sum / channels

            square_sum += sample * sample
            peak_amplitude = max(peak_amplitude, abs(sample))
            if previous_sample is not None and (sample >= 0.0) != (previous_sample >= 0.0):
                zero_crossings += 1
            previous_sample = sample

            window_square_sum += sample * sample
            samples_in_window += 1
            if samples_in_window == window_samples:
                window_rms = math.sqrt(window_square_sum / samples_in_window)
                silence_windows += window_rms <= self._silence_rms_threshold
                total_windows += 1
                window_square_sum = 0.0
                samples_in_window = 0

        if samples_in_window:
            window_rms = math.sqrt(window_square_sum / samples_in_window)
            silence_windows += window_rms <= self._silence_rms_threshold
            total_windows += 1

        rms_amplitude = math.sqrt(square_sum / frame_count)
        features = {
            "duration_seconds": frame_count / sample_rate,
            "sample_rate_hz": float(sample_rate),
            "rms_amplitude": rms_amplitude,
            "peak_amplitude": peak_amplitude,
            "zero_crossing_rate": zero_crossings / max(frame_count - 1, 1),
            "silence_ratio": silence_windows / total_windows,
        }
        return VocalSignalAnalysis(
            event_id=event_id,
            conversation_id=conversation_id,
            source="pcm-wav-signal-analyzer",
            features=features,
        )