import io
import struct
import unittest
import wave

from aura.voice import AudioRecording, ResidentVoiceSession
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.adapters.wav_signal_analyzer import (
    AudioSignalAnalysisError,
    PcmWavSignalAnalyzer,
)
from aura.voice.testing import MockSpeaker


def make_wav(samples, *, sample_rate=1000, channels=1, sample_width=2):
    raw = struct.pack("<" + "h" * len(samples), *samples) if sample_width == 2 else bytes(samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw)
    return buffer.getvalue()


class PcmWavSignalAnalyzerTests(unittest.TestCase):
    def test_silence_has_zero_energy_and_full_silence_ratio(self):
        recording = AudioRecording(make_wav([0] * 100), "silence.wav", "audio/wav")

        result = PcmWavSignalAnalyzer().analyze(
            recording,
            event_id="event-1",
            conversation_id="conversation-1",
        )

        self.assertEqual(result.event_id, "event-1")
        self.assertEqual(result.conversation_id, "conversation-1")
        self.assertEqual(result.features["duration_seconds"], 0.1)
        self.assertEqual(result.features["rms_amplitude"], 0.0)
        self.assertEqual(result.features["peak_amplitude"], 0.0)
        self.assertEqual(result.features["silence_ratio"], 1.0)
        self.assertEqual(result.source, "pcm-wav-signal-analyzer")

    def test_alternating_wave_has_energy_and_zero_crossings(self):
        recording = AudioRecording(
            make_wav([12000, -12000] * 50),
            "voice.wav",
            "audio/wav",
        )

        result = PcmWavSignalAnalyzer().analyze(
            recording,
            event_id="event-1",
            conversation_id="conversation-1",
        )

        self.assertGreater(result.features["rms_amplitude"], 0.3)
        self.assertGreater(result.features["zero_crossing_rate"], 0.9)
        self.assertGreater(result.features["peak_amplitude"], 0.3)
        self.assertEqual(result.features["silence_ratio"], 0.0)

    def test_stereo_samples_are_downmixed_for_measurement(self):
        recording = AudioRecording(
            make_wav([10000, -10000] * 50, channels=2),
            "stereo.wav",
            "audio/wav",
        )

        result = PcmWavSignalAnalyzer().analyze(
            recording,
            event_id="event-1",
            conversation_id="conversation-1",
        )

        self.assertEqual(result.features["rms_amplitude"], 0.0)

    def test_rejects_invalid_wav_format_and_duration_or_size_over_limit(self):
        eight_bit_recording = AudioRecording(
            make_wav([128] * 100, sample_width=1),
            "8bit.wav",
            "audio/wav",
        )
        with self.assertRaisesRegex(AudioSignalAnalysisError, "16-bit PCM"):
            PcmWavSignalAnalyzer().analyze(
                eight_bit_recording,
                event_id="event-1",
                conversation_id="conversation-1",
            )

        long_recording = AudioRecording(
            make_wav([0] * 101, sample_rate=100),
            "long.wav",
            "audio/wav",
        )
        with self.assertRaisesRegex(AudioSignalAnalysisError, "max_duration_seconds"):
            PcmWavSignalAnalyzer(max_duration_seconds=1.0).analyze(
                long_recording,
                event_id="event-1",
                conversation_id="conversation-1",
            )

        with self.assertRaisesRegex(AudioSignalAnalysisError, "max_audio_bytes"):
            PcmWavSignalAnalyzer(max_audio_bytes=8).analyze(
                eight_bit_recording,
                event_id="event-1",
                conversation_id="conversation-1",
            )

    def test_session_returns_correlated_transcript_and_signal_analysis(self):
        recording = AudioRecording(
            make_wav([2000, -2000] * 50),
            "reply.wav",
            "audio/wav",
        )
        session = ResidentVoiceSession(
            MockSpeaker(),
            FakeSpeechToTextAdapter("I am okay"),
            conversation_id_factory=lambda: "conversation-2",
            signal_analyzer=PcmWavSignalAnalyzer(),
        )
        prompt = session.ask("event-2", "Are you okay?")

        reply = session.process_reply(prompt.conversation_id, recording, language="en")

        self.assertEqual(reply.event_id, "event-2")
        self.assertEqual(reply.conversation_id, "conversation-2")
        self.assertEqual(reply.transcript.text, "I am okay")
        self.assertEqual(reply.signal_analysis.event_id, reply.event_id)
        self.assertEqual(reply.signal_analysis.conversation_id, reply.conversation_id)
        self.assertEqual(session.pending_prompts, ())

    def test_signal_analysis_failure_leaves_prompt_pending(self):
        recording = AudioRecording(b"not a wav", "bad.wav", "audio/wav")
        session = ResidentVoiceSession(
            MockSpeaker(),
            FakeSpeechToTextAdapter("reply"),
            conversation_id_factory=lambda: "conversation-3",
            signal_analyzer=PcmWavSignalAnalyzer(),
        )
        prompt = session.ask("event-3", "Please respond")

        with self.assertRaises(AudioSignalAnalysisError):
            session.process_reply(prompt.conversation_id, recording)
        self.assertEqual(session.pending_prompts, (prompt,))


if __name__ == "__main__":
    unittest.main()