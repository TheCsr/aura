import unittest

from aura.contracts import Transcript
from aura.voice import AudioRecording, ResidentVoiceSession, SpeakerReceipt
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.testing import MockMicrophone, MockSpeaker, MockVoiceTranscriber


class VoiceComponentTests(unittest.TestCase):
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