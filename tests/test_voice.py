import unittest
import os
from unittest.mock import Mock, patch
from pathlib import Path

import requests

from aura.contracts import Transcript
from aura.voice import AudioRecording, ResidentVoiceSession, SpeakerReceipt
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.testing import MockMicrophone, MockSpeaker, MockVoiceTranscriber
from aura.voice.adapters.orange_kb_whisper import OrangeKbWhisperAdapter


class VoiceComponentTests(unittest.TestCase):
    def test_resident_launcher_uses_env_host_and_port_without_starting_server(self):
        import run_resident_device
        import sys

        with patch.dict(os.environ, {
            "AURA_DEVICE_HOST": "127.0.0.1",
            "AURA_DEVICE_PORT": "8517",
        }, clear=True), patch("run_resident_device._load_env"), patch(
            "run_resident_device.subprocess.call", return_value=0
        ) as call:
            result = run_resident_device.main()

        self.assertEqual(result, 0)
        command = call.call_args.args[0]
        self.assertEqual(command[0:3], [sys.executable, "-m", "streamlit"])
        self.assertEqual(command[-4:], ["--server.address", "127.0.0.1", "--server.port", "8517"])

    @patch("aura.voice.adapters.orange_kb_whisper.requests.post")
    def test_whisper_adapter_uses_bser_url_when_kb_whisper_url_is_unset(self, post):
        post.return_value.json.return_value = {"text": "Hej."}
        with patch.dict(os.environ, {"BSER_URL": "https://speech.example", "ORANGE_API_KEY": "test-key"}, clear=True):
            adapter = OrangeKbWhisperAdapter()
            adapter.transcribe(AudioRecording(b"audio", "reply.wav", "audio/wav"))

        self.assertEqual(post.call_args.args[0], "https://speech.example/v1/audio/transcriptions")

    def test_kb_whisper_url_takes_precedence_over_bser_url(self):
        with patch.dict(os.environ, {
            "KB_WHISPER_URL": "https://preferred.example/",
            "BSER_URL": "https://legacy.example",
        }, clear=True):
            adapter = OrangeKbWhisperAdapter()

        self.assertEqual(adapter._base_url, "https://preferred.example")

    def test_resident_app_forwards_correlated_playback_once_without_synthesis(self):
        from streamlit.testing.v1 import AppTest

        pending = {
            "subject_id": "synthetic-resident-1", "event_id": "speaker-event",
            "conversation_id": "speaker-conversation", "text": "Hej!",
        }
        receipt = {**pending, "action": "playback", "provider": "browser_speech", "status": "completed", "notification_id": "receipt-1"}
        response = Mock()
        response.json.return_value = {"prompt": pending, "whisper_configured": True}
        component = Mock(return_value=receipt)
        with patch("requests.get", return_value=response), patch("requests.post") as post, patch("gtts.gTTS") as tts, patch("streamlit.components.v1.declare_component", return_value=component):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "resident_device_app.py"))
            app.run(timeout=10)
            app.run(timeout=10)
            self.assertEqual(len(app.exception), 0)
            post.assert_called_once()
            self.assertTrue(post.call_args.args[0].endswith("/api/device/playback"))
            self.assertEqual(post.call_args.kwargs["json"], {
                **{key: pending[key] for key in ("subject_id", "event_id", "conversation_id")},
                "provider": "browser_speech", "status": "completed",
            })
            tts.assert_not_called()
            self.assertEqual(component.call_args.kwargs["key"], "resident-speaker")

    def test_resident_app_ignores_stale_playback_receipts(self):
        from streamlit.testing.v1 import AppTest

        pending = {
            "subject_id": "synthetic-resident-1", "event_id": "speaker-event",
            "conversation_id": "speaker-conversation", "text": "Hej!",
        }
        receipt = {**pending, "conversation_id": "old-conversation", "action": "playback", "provider": "browser_speech", "status": "completed", "notification_id": "receipt-1"}
        response = Mock()
        response.json.return_value = {"prompt": pending, "whisper_configured": True}
        with patch("requests.get", return_value=response), patch("requests.post") as post, patch("streamlit.components.v1.declare_component", return_value=Mock(return_value=receipt)):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "resident_device_app.py"))
            app.run(timeout=10)
            self.assertEqual(len(app.exception), 0)
            post.assert_not_called()

    def test_resident_app_synthesizes_fallback_only_on_request_without_delivery_claim(self):
        from streamlit.testing.v1 import AppTest

        pending = {
            "subject_id": "synthetic-resident-1", "event_id": "speaker-event",
            "conversation_id": "speaker-conversation", "text": "Hej!",
        }
        receipt = {**pending, "action": "fallback", "provider": "gtts", "status": "requested", "notification_id": "receipt-1"}
        response = Mock()
        response.json.return_value = {"prompt": pending, "whisper_configured": True}
        with patch("requests.get", return_value=response), patch("requests.post") as post, patch("gtts.gTTS") as tts, patch("streamlit.rerun"), patch("streamlit.components.v1.declare_component", return_value=Mock(return_value=receipt)):
            tts.return_value.write_to_fp.side_effect = lambda buffer: buffer.write(b"actual-mp3")
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "resident_device_app.py"))
            app.run(timeout=10)
            self.assertEqual(len(app.exception), 0)
            tts.assert_called_once_with(text="Hej!", lang="sv", slow=False, timeout=(5, 20))
            self.assertEqual(app.session_state["speaker_audio"], "YWN0dWFsLW1wMw==")
            post.assert_not_called()

    def test_resident_app_handles_no_prompt_and_pending_prompt_without_speech_calls(self):
        from streamlit.testing.v1 import AppTest

        pending = {
            "subject_id": "synthetic-resident-1",
            "event_id": "screen-event",
            "conversation_id": "screen-conversation",
            "text": "Har du det bra?",
            "remaining_reply_seconds": 118,
        }
        for prompt in (None, pending):
            response = Mock()
            response.json.return_value = {"prompt": prompt, "whisper_configured": True}
            with self.subTest(prompt=prompt), patch("requests.get", return_value=response), patch("requests.post") as post, patch("gtts.gTTS") as tts:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "resident_device_app.py"))
                app.run(timeout=10)
                self.assertEqual(len(app.exception), 0)
                if prompt is not None:
                    self.assertIn("Time remaining: 01:58", [item.value for item in app.caption])
                post.assert_not_called()
                tts.assert_not_called()

    def test_resident_prompt_is_rendered_as_text_not_html(self):
        from streamlit.testing.v1 import AppTest

        unsafe_looking_text = 'Är allt bra? <script>alert("test")</script>'
        pending = {
            "subject_id": "synthetic-resident-1",
            "event_id": "screen-event",
            "conversation_id": "screen-conversation",
            "text": unsafe_looking_text,
        }
        response = Mock()
        response.json.return_value = {"prompt": pending, "whisper_configured": True}
        with patch("requests.get", return_value=response), patch("requests.post"):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "resident_device_app.py"))
            app.run(timeout=10)

        self.assertEqual(len(app.exception), 0)
        self.assertIn(unsafe_looking_text, [element.value for element in app.text])
        self.assertFalse(any("<script>" in element.value for element in app.markdown))

    @patch("aura.voice.adapters.orange_kb_whisper.requests.post")
    def test_real_stt_adapter_uploads_actual_recording_once(self, post):
        post.return_value.json.return_value = {"text": " Hjälp mig. "}
        adapter = OrangeKbWhisperAdapter(api_key="test-key", base_url="https://speech.example", model="test-model")
        recording = AudioRecording(b"actual-upload", "reply.wav", "audio/wav")
        transcript = adapter.transcribe(recording, language="sv")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["files"]["file"][1], recording.audio)
        self.assertEqual(transcript.text, "Hjälp mig.")
        self.assertEqual(transcript.source, "orange-kb-whisper")

    @patch("aura.voice.adapters.orange_kb_whisper.requests.post")
    def test_real_stt_adapter_rejects_empty_output_and_sanitizes_transport_errors(self, post):
        adapter = OrangeKbWhisperAdapter(api_key="test-key")
        recording = AudioRecording(b"audio", "reply.wav", "audio/wav")
        for payload in ({"text": " "}, {}, {"text": 42}, []):
            post.return_value.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                adapter.transcribe(recording)
        post.side_effect = requests.Timeout("private-url test-key")
        with self.assertRaises(RuntimeError) as raised:
            adapter.transcribe(recording)
        self.assertNotIn("test-key", str(raised.exception))
        self.assertNotIn("private-url", str(raised.exception))

    def test_mock_speaker_records_prompt_without_claiming_audio_delivery(self):
        speaker = MockSpeaker()

        receipt = speaker.speak("Hej, är allt bra?")

        self.assertEqual(receipt, SpeakerReceipt("Hej, är allt bra?", "mock_text_preview", False))
        self.assertEqual(speaker.receipts, (receipt,))

    def test_voice_session_captures_and_transcribes_mock_response(self):
        recording = AudioRecording(b"captured wav bytes", "reply.wav", "audio/wav")
        microphone = MockMicrophone(recording)
        transcriber = FakeSpeechToTextAdapter("Jag mår bra.")
        session = ResidentVoiceSession(
            MockSpeaker(), transcriber, conversation_id_factory=lambda: "conversation-1"
        )

        prompt = session.ask("event-1", "Är allt bra?")
        captured = microphone.capture()
        transcript = session.transcribe_reply(prompt.conversation_id, captured, language="sv")

        self.assertEqual(prompt.event_id, "event-1")
        self.assertEqual(transcript.conversation_id, "conversation-1")
        self.assertEqual(transcript.event_id, "event-1")
        self.assertEqual(transcript.transcript, Transcript("Jag mår bra.", "fake-stt", "sv", "scripted-fake"))
        self.assertEqual(captured, recording)
        self.assertEqual(transcriber.calls, [("reply.wav", "sv", len(recording.audio))])
        self.assertEqual(session.pending_prompts, ())

    def test_out_of_order_replies_keep_their_event_correlation(self):
        transcriber = FakeSpeechToTextAdapter("reply for event two")
        session = ResidentVoiceSession(
            MockSpeaker(),
            transcriber,
            conversation_id_factory=iter(("conversation-1", "conversation-2")).__next__,
        )
        first = session.ask("event-1", "Prompt one")
        second = session.ask("event-2", "Prompt two")
        recording = AudioRecording(b"audio", "reply.wav", "audio/wav")

        reply = session.transcribe_reply(second.conversation_id, recording)

        self.assertEqual(reply.event_id, "event-2")
        self.assertEqual(reply.conversation_id, second.conversation_id)
        self.assertEqual(session.pending_prompts, (first,))

    def test_unknown_or_already_answered_conversation_is_rejected(self):
        transcriber = FakeSpeechToTextAdapter("reply")
        session = ResidentVoiceSession(
            MockSpeaker(), transcriber, conversation_id_factory=lambda: "conversation-1"
        )
        prompt = session.ask("event-1", "Prompt")
        recording = AudioRecording(b"audio", "reply.wav", "audio/wav")

        with self.assertRaisesRegex(ValueError, "No pending prompt"):
            session.transcribe_reply("unknown-conversation", recording)
        session.transcribe_reply(prompt.conversation_id, recording)
        with self.assertRaisesRegex(ValueError, "No pending prompt"):
            session.transcribe_reply(prompt.conversation_id, recording)

    def test_transcription_failure_keeps_conversation_pending_for_retry(self):
        transcriber = FakeSpeechToTextAdapter()
        session = ResidentVoiceSession(
            MockSpeaker(), transcriber, conversation_id_factory=lambda: "conversation-1"
        )
        prompt = session.ask("event-1", "Prompt")
        recording = AudioRecording(b"audio", "reply.wav", "audio/wav")

        with self.assertRaisesRegex(ValueError, "Set a fake transcript"):
            session.transcribe_reply(prompt.conversation_id, recording)
        self.assertEqual(session.pending_prompts, (prompt,))

        transcriber.set_transcript("Retry succeeded")
        result = session.transcribe_reply(prompt.conversation_id, recording)
        self.assertEqual(result.event_id, "event-1")
        self.assertEqual(session.pending_prompts, ())

    def test_fake_transcriber_requires_scripted_text(self):
        recording = AudioRecording(b"audio", "reply.wav", "audio/wav")

        with self.assertRaisesRegex(ValueError, "Set a fake transcript"):
            FakeSpeechToTextAdapter().transcribe(recording, language="sv")

    def test_legacy_mock_transcriber_name_remains_an_alias(self):
        self.assertIs(MockVoiceTranscriber, FakeSpeechToTextAdapter)

    def test_audio_recording_requires_nonempty_data(self):
        with self.assertRaisesRegex(ValueError, "captured bytes"):
            AudioRecording(b"", "reply.wav", "audio/wav")


if __name__ == "__main__":
    unittest.main()