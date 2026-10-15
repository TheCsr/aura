import json
from io import BytesIO
import unittest
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dataclasses import replace

from aura.graph import WorkflowOutcome
from aura.agents.critical_risk_policy import CriticalRiskPolicy
from aura.messaging.codec import EventMessageCodec
from aura.streaming.composition import DEFAULT_BASELINE, DEFAULT_SUBJECT_ID, create_streaming_demo
from aura.streaming.demo import make_sensor_event, result_as_json
from aura.contracts import AssessmentSeverity, Transcript, TriageStage
from aura.agents.triage import TriageAgent
from aura.testing import DeterministicFakeChatModel
from aura.streaming.voice import SyntheticSignalMicrophone
from aura.voice.contracts import AudioRecording
from aura.voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer


class StreamingDemoTests(unittest.TestCase):
    def resume(self, demo, event, prompt, transcriber=None, recording=None):
        return demo.service.resume_audio(
            event_id=event.event_id, subject_id=event.subject_id,
            conversation_id=prompt.conversation_id,
            recording=recording or SyntheticSignalMicrophone().capture(),
            transcriber=transcriber or Mock(transcribe=Mock(return_value=Transcript("Jag mår bra hemma.", "orange-kb-whisper", "sv"))),
            signal_analyzer=PcmWavSignalAnalyzer(),
        )

    def test_real_audio_resumes_original_assessment_and_duplicate_does_not_dispatch_twice(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-original")
        with patch.object(demo.service, "_supervisor", wraps=demo.service._supervisor) as supervisor:
            pending = demo.service.start(event)
            transcriber = Mock()
            transcriber.transcribe.return_value = Transcript("Fryser hemma.", "orange-kb-whisper", "sv")
            result = self.resume(demo, event, pending.voice_prompt, transcriber)
            repeated = self.resume(demo, event, pending.voice_prompt, transcriber)
        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertIs(result.assessment, pending.assessment)
        self.assertIs(result, repeated)
        self.assertEqual(result.voice_reply.transcript.source, "orange-kb-whisper")
        self.assertEqual(result.proposal.triage_stage, TriageStage.POST_REPLY)
        supervisor.assert_called_once()
        transcriber.transcribe.assert_called_once()
        self.assertEqual(len(demo.dashboard.records), 1)
        self.assertEqual(demo.service.pending_prompts(event.subject_id), ())

    def test_out_of_order_replies_do_not_overwrite_pending_events(self):
        demo = create_streaming_demo()
        first = make_sensor_event("critical-cold", event_id="audio-first")
        second = make_sensor_event("door-open", event_id="audio-second")
        first_prompt = demo.service.start(first).voice_prompt
        second_prompt = demo.service.start(second).voice_prompt
        self.resume(demo, second, second_prompt)
        self.assertEqual(demo.service.pending_prompts(first.subject_id), (first_prompt,))
        self.assertEqual(demo.service.pending_prompts("different-resident"), ())

    def test_mismatched_resident_event_and_unknown_conversation_are_rejected_before_stt(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-bound")
        prompt = demo.service.start(event).voice_prompt
        transcriber = Mock()
        for wrong_event, wrong_prompt in (
            (replace(event, subject_id="another-resident"), prompt),
            (replace(event, event_id="another-event"), prompt),
            (event, replace(prompt, conversation_id="unknown")),
        ):
            with self.subTest(event=wrong_event), self.assertRaisesRegex(ValueError, "does not match"):
                self.resume(demo, wrong_event, wrong_prompt, transcriber)
        transcriber.transcribe.assert_not_called()
        self.assertEqual(demo.dashboard.records, ())

    def test_no_reply_timeout_escalates_once_and_rejects_late_reply(self):
        demo = create_streaming_demo()
        demo.service._critical_voice_timeout_seconds = 120.0
        event = make_sensor_event("critical-cold", event_id="audio-timeout")
        with patch.object(demo.service, "_clock", return_value=0):
            prompt = demo.service.start(event).voice_prompt
        with patch.object(demo.service, "_clock", return_value=121):
            escalated = demo.service.sweep_expired_conversations()
            self.assertEqual(len(escalated), 1)
            self.assertEqual(demo.service.sweep_expired_conversations(), ())
            self.assertEqual(demo.service.pending_prompts(event.subject_id), ())
            with self.assertRaisesRegex(ValueError, "no longer awaiting"):
                self.resume(demo, event, prompt)

        result = demo.service.results()[0]
        self.assertEqual(result.outcome, WorkflowOutcome.ESCALATED_NO_REPLY)
        self.assertEqual(result.proposal.action_type, "urgent_visit")
        self.assertEqual(result.proposal.parameters["reason_code"], "no_resident_reply")
        self.assertEqual(result.decision.status.value, "approve")
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_moderate_no_reply_uses_configured_timeout_and_remote_followup(self):
        demo = create_streaming_demo()
        demo.service._moderate_voice_timeout_seconds = 600.0
        first = make_sensor_event("normal", event_id="moderate-timeout-first")
        first.observations["temperature_c"] = 21.0
        second = replace(
            first,
            event_id="moderate-timeout-pending",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )
        with patch.object(demo.service, "_clock", return_value=0):
            demo.service.start(first)
            pending = demo.service.start(second)
        self.assertEqual(demo.service.reply_timeout_seconds(pending.voice_prompt.conversation_id), 600)

        with patch.object(demo.service, "_clock", return_value=599):
            self.assertEqual(demo.service.sweep_expired_conversations(), ())
        with patch.object(demo.service, "_clock", return_value=600):
            escalated = demo.service.sweep_expired_conversations()

        self.assertEqual(len(escalated), 1)
        self.assertEqual(escalated[0].outcome, WorkflowOutcome.ESCALATED_NO_REPLY)
        self.assertEqual(escalated[0].proposal.action_type, "remote_followup")
        self.assertEqual(escalated[0].proposal.parameters["reason_code"], "no_resident_reply")
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_reply_timeout_environment_settings_are_applied(self):
        with patch.dict("os.environ", {
            "AURA_REPLY_TIMEOUT_CRITICAL_S": "45",
            "AURA_REPLY_TIMEOUT_MODERATE_S": "240",
        }):
            demo = create_streaming_demo()
            critical = make_sensor_event("critical-cold", event_id="timeout-env-critical")
            critical_prompt = demo.service.start(critical).voice_prompt
            self.assertEqual(demo.service.reply_timeout_seconds(critical_prompt.conversation_id), 45)

            moderate = make_sensor_event("normal", event_id="timeout-env-moderate-first")
            moderate.observations["temperature_c"] = 21.0
            confirmed = replace(
                moderate,
                event_id="timeout-env-moderate-confirmed",
                occurred_at=moderate.occurred_at + timedelta(seconds=30),
            )
            demo.service.start(moderate)
            moderate_prompt = demo.service.start(confirmed).voice_prompt
            self.assertEqual(demo.service.reply_timeout_seconds(moderate_prompt.conversation_id), 240)

    def test_stt_failure_and_empty_transcript_leave_conversation_pending(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-retry")
        prompt = demo.service.start(event).voice_prompt
        transcriber = Mock()
        transcriber.transcribe.side_effect = RuntimeError("provider unavailable")
        with self.assertRaises(RuntimeError):
            self.resume(demo, event, prompt, transcriber)
        transcriber.transcribe.side_effect = None
        transcriber.transcribe.return_value = SimpleNamespace(
            text=" ", source="orange-kb-whisper"
        )
        with self.assertRaisesRegex(ValueError, "No speech"):
            self.resume(demo, event, prompt, transcriber)
        self.assertEqual(demo.service.pending_prompts(event.subject_id), (prompt,))
        self.assertEqual(demo.dashboard.records, ())
        transcriber.transcribe.return_value = Transcript("Svar", "orange-kb-whisper")
        self.assertEqual(self.resume(demo, event, prompt, transcriber).outcome, WorkflowOutcome.DISPATCHED)

    def test_invalid_wav_does_not_call_stt(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-invalid")
        prompt = demo.service.start(event).voice_prompt
        transcriber = Mock()
        with self.assertRaises(ValueError):
            self.resume(demo, event, prompt, transcriber, AudioRecording(b"not-wav", "reply.wav", "audio/wav"))
        transcriber.transcribe.assert_not_called()

    def test_silent_wav_does_not_call_stt(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-silent")
        prompt = demo.service.start(event).voice_prompt
        audio = BytesIO()
        with wave.open(audio, "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16000)
            recording.writeframes(b"\x00" * 3200)
        transcriber = Mock()
        with self.assertRaisesRegex(ValueError, "silent"):
            self.resume(demo, event, prompt, transcriber, AudioRecording(audio.getvalue(), "silent.wav", "audio/wav"))
        transcriber.transcribe.assert_not_called()
        self.assertEqual(demo.dashboard.records, ())

    def test_new_recording_cannot_replace_already_transcribed_reply(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-immutable")
        prompt = demo.service.start(event).voice_prompt
        self.resume(demo, event, prompt)
        transcriber = Mock()
        different_audio = SyntheticSignalMicrophone(duration_seconds=0.5).capture()
        with self.assertRaisesRegex(ValueError, "already has a transcribed recording"):
            self.resume(demo, event, prompt, transcriber, different_audio)
        transcriber.transcribe.assert_not_called()
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_successful_stt_is_cached_when_triage_fails(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="audio-agent-retry")
        prompt = demo.service.start(event).voice_prompt
        transcriber = Mock()
        transcriber.transcribe.return_value = Transcript("Svar", "orange-kb-whisper")
        with patch.object(demo.service, "_triage", side_effect=RuntimeError("LLM unavailable")):
            with self.assertRaises(RuntimeError):
                self.resume(demo, event, prompt, transcriber)
        self.resume(demo, event, prompt, transcriber)
        transcriber.transcribe.assert_called_once()

    def test_moderate_event_is_remote_followed_up_after_actual_reply(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="audio-held")
        event.observations["temperature_c"] = 22.4
        first = demo.service.start(event)
        confirming_event = replace(
            event,
            event_id="audio-held-confirmed",
            occurred_at=event.occurred_at + timedelta(seconds=60),
        )
        prompt = demo.service.start(confirming_event).voice_prompt
        result = self.resume(demo, confirming_event, prompt)
        self.assertEqual(first.outcome, WorkflowOutcome.DROPPED)
        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.proposal.action_type, "remote_followup")
        self.assertEqual(result.decision.reason_codes, ("remote_followup_approved",))
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_device_intake_waits_without_synthetic_reply_or_ticket(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="device-pending")
        result = demo.service.start(event)
        self.assertEqual(result.outcome, WorkflowOutcome.AWAITING_RESIDENT)
        self.assertIsNone(result.voice_reply)
        self.assertEqual(result.proposal.action_type, "ask_resident")
        self.assertEqual(result.decision.status.value, "approve")
        self.assertEqual(result.voice_prompt.text, result.proposal.parameters["question_sv"])
        self.assertEqual(demo.dashboard.records, ())
        self.assertIs(demo.service.start(event), result)
        self.assertEqual(demo.service.pending_prompts(event.subject_id), (result.voice_prompt,))

    def test_normal_sensor_event_short_circuits_without_voice_or_dashboard(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="event-normal")

        result = demo.service.handle(event)
        output = json.loads(result_as_json(result))

        self.assertEqual(result.outcome, WorkflowOutcome.NO_ACTION)
        self.assertEqual(result.assessment.drop_reason, "within_baseline")
        self.assertEqual(output["supervisor"]["drop_reason"], "within_baseline")
        self.assertIsNone(result.voice_prompt)
        self.assertEqual(demo.dashboard.records, ())

    def test_critical_sensor_event_runs_voice_guardian_and_dashboard_pipeline(self):
        demo = create_streaming_demo(resident_response="Jag behöver hjälp.")
        event = make_sensor_event("critical-cold", event_id="event-critical")

        result = demo.service.handle(event)
        output = json.loads(result_as_json(result))

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.voice_prompt.event_id, event.event_id)
        self.assertEqual(result.voice_reply.event_id, event.event_id)
        self.assertEqual(
            result.voice_prompt.conversation_id,
            result.voice_reply.conversation_id,
        )
        self.assertEqual(result.voice_reply.transcript.text, "Jag behöver hjälp.")
        self.assertIn("rms_amplitude", result.voice_reply.signal_analysis.features)
        self.assertEqual(result.proposal.action_type, "request_human_review")
        self.assertEqual(result.proposal.parameters["priority"], "Urgent")
        self.assertEqual(result.decision.reason_codes, ("resident_reply_distress",))
        self.assertEqual(len(demo.dashboard.records), 1)
        self.assertEqual(demo.dashboard.records[0].priority, "Urgent")
        self.assertEqual(
            output["dashboard_record_id"],
            demo.dashboard.records[0].record_id,
        )

    def test_broker_encoded_sensor_event_runs_full_streaming_pipeline(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="event-codec-critical")
        broker_event = EventMessageCodec.decode(EventMessageCodec.encode(event))

        result = demo.service.handle(broker_event)

        self.assertEqual(result.event_id, event.event_id)
        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_noncritical_anomaly_gets_voice_check_but_does_not_update_dashboard(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="event-moderate-temperature")
        event.observations["temperature_c"] = 21.0

        first = demo.service.handle(event)
        repeated = replace(
            event,
            event_id="event-moderate-temperature-repeat",
            occurred_at=event.occurred_at + timedelta(seconds=60),
        )
        result = demo.service.handle(repeated)

        self.assertEqual(first.outcome, WorkflowOutcome.DROPPED)
        self.assertEqual(first.assessment.drop_reason, "awaiting_persistence")
        first_output = json.loads(result_as_json(first))
        self.assertEqual(first_output["outcome"], "dropped")
        self.assertEqual(first_output["supervisor"]["drop_reason"], "awaiting_persistence")
        self.assertEqual(result.outcome, WorkflowOutcome.HELD)
        self.assertIsNotNone(result.voice_reply)
        self.assertEqual(result.decision.reason_codes, ("critical_threshold_not_met",))
        self.assertEqual(demo.dashboard.records, ())

    def test_noncritical_deviation_waits_for_second_reading_before_voice_prompt(self):
        demo = create_streaming_demo()
        first = make_sensor_event("normal", event_id="debounce-first")
        first.observations["temperature_c"] = 21.0
        second = replace(
            first,
            event_id="debounce-second",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )

        dropped = demo.service.start(first)
        prompted = demo.service.start(second)

        self.assertEqual(dropped.outcome, WorkflowOutcome.DROPPED)
        self.assertEqual(dropped.assessment.severity, AssessmentSeverity.MODERATE)
        self.assertEqual(dropped.assessment.drop_reason, "awaiting_persistence")
        self.assertEqual(prompted.outcome, WorkflowOutcome.AWAITING_RESIDENT)
        self.assertIsNotNone(prompted.voice_prompt)

    def test_initial_dismiss_is_recorded_without_dispatch(self):
        demo = create_streaming_demo()
        demo.service._triage = TriageAgent(DeterministicFakeChatModel(
            scripted_responses={"initial": {
                "action_type": "dismiss",
                "rationale": "The confirmed sensor change does not need follow-up.",
                "confidence": 0.9,
                "parameters": {},
            }}
        ))
        first = make_sensor_event("normal", event_id="dismiss-first")
        first.observations["temperature_c"] = 21.0
        second = replace(
            first,
            event_id="dismiss-confirmed",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )

        demo.service.start(first)
        result = demo.service.start(second)
        payload = json.loads(result_as_json(result))

        self.assertEqual(result.outcome, WorkflowOutcome.DISMISSED)
        self.assertEqual(result.proposal.triage_stage, TriageStage.INITIAL)
        self.assertEqual(result.decision.status.value, "approve")
        self.assertIsNotNone(result.receipt)
        self.assertEqual(payload["outcome"], "dismissed")
        self.assertIsNone(payload["dashboard_record_id"])
        self.assertEqual(payload["action_record_id"], result.receipt.external_reference)
        self.assertEqual(demo.dashboard.records, ())
        self.assertEqual(len(demo.dashboard.action_records), 1)
        self.assertEqual(demo.dashboard.action_records[0].record_type, "dismissal_audit")

    def test_initial_remote_followup_is_guardian_approved_and_dispatched(self):
        demo = create_streaming_demo()
        demo.service._triage = TriageAgent(DeterministicFakeChatModel(
            scripted_responses={"initial": {
                "action_type": "remote_followup",
                "rationale": "The confirmed change merits a remote follow-up.",
                "confidence": 0.9,
                "parameters": {},
            }}
        ))
        first = make_sensor_event("normal", event_id="followup-first")
        first.observations["temperature_c"] = 21.0
        second = replace(
            first,
            event_id="followup-confirmed",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )

        demo.service.start(first)
        result = demo.service.start(second)

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.proposal.action_type, "remote_followup")
        self.assertEqual(demo.dashboard.records[0].action_type, "remote_followup")

    def test_explicit_negative_reply_creates_urgent_human_review_ticket(self):
        demo = create_streaming_demo()
        first = make_sensor_event("normal", event_id="distress-first")
        first.observations["temperature_c"] = 21.0
        confirming = replace(
            first,
            event_id="distress-confirmed",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )
        demo.service.start(first)
        pending = demo.service.start(confirming)
        transcriber = Mock(transcribe=Mock(
            return_value=Transcript("Det är inte bra hemma.", "orange-kb-whisper", "sv")
        ))

        result = self.resume(demo, confirming, pending.voice_prompt, transcriber)

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.proposal.action_type, "request_human_review")
        self.assertEqual(result.proposal.parameters["priority"], "Urgent")
        self.assertEqual(result.proposal.parameters["reason_code"], "resident_reply_distress")
        self.assertEqual(result.decision.reason_codes, ("resident_reply_distress",))
        self.assertEqual(demo.dashboard.records[0].priority, "Urgent")

    def test_reassuring_negated_reply_does_not_trigger_urgent_escalation(self):
        demo = create_streaming_demo()
        first = make_sensor_event("normal", event_id="reassuring-first")
        first.observations["temperature_c"] = 21.0
        confirming = replace(
            first,
            event_id="reassuring-confirmed",
            occurred_at=first.occurred_at + timedelta(seconds=30),
        )
        demo.service.start(first)
        pending = demo.service.start(confirming)
        transcriber = Mock(transcribe=Mock(
            return_value=Transcript("Jag har inte ont, allt är bra hemma.", "orange-kb-whisper", "sv")
        ))

        result = self.resume(demo, confirming, pending.voice_prompt, transcriber)

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.proposal.action_type, "remote_followup")
        self.assertNotIn("reason_code", result.proposal.parameters)
        self.assertEqual(demo.dashboard.records[0].priority, "Normal")

    def test_initial_home_and_urgent_visits_dispatch_with_action_priorities(self):
        cases = (
            ("home_visit", "High", "moderate"),
            ("urgent_visit", "Urgent", "critical"),
        )
        for action_type, priority, severity in cases:
            with self.subTest(action_type=action_type):
                demo = create_streaming_demo()
                demo.service._triage = TriageAgent(DeterministicFakeChatModel(
                    scripted_responses={"initial": {
                        "action_type": action_type,
                        "rationale": f"Synthetic {action_type} proposal.",
                        "confidence": 0.95,
                        "parameters": {},
                    }}
                ))
                if severity == "critical":
                    result = demo.service.start(
                        make_sensor_event("critical-cold", event_id=f"first-pass-{action_type}")
                    )
                else:
                    first = make_sensor_event("normal", event_id=f"first-pass-{action_type}-first")
                    first.observations["temperature_c"] = 21.0
                    confirming = replace(
                        first,
                        event_id=f"first-pass-{action_type}-confirmed",
                        occurred_at=first.occurred_at + timedelta(seconds=30),
                    )
                    demo.service.start(first)
                    result = demo.service.start(confirming)

                self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
                self.assertEqual(result.proposal.action_type, action_type)
                self.assertEqual(result.proposal.triage_stage, TriageStage.INITIAL)
                self.assertEqual(demo.dashboard.records[0].priority, priority)

    def test_resident_request_for_family_contact_creates_guardian_approved_action_record(self):
        subject_id = DEFAULT_SUBJECT_ID
        demo = create_streaming_demo(
            guardian_policy=CriticalRiskPolicy(resident_contexts={
                subject_id: {
                    "relative_contact": {
                        "name": "Synthetic family contact",
                        "phone": "demo-only",
                    }
                }
            }),
        )
        demo.service._triage = TriageAgent(DeterministicFakeChatModel(
            scripted_responses={
                "initial": {
                    "action_type": "ask_resident",
                    "rationale": "The door sensor needs a resident check-in.",
                    "confidence": 0.9,
                    "parameters": {"question_sv": "Är allt bra hemma?"},
                },
                "post_reply": {
                    "action_type": "notify_relative",
                    "rationale": 'Residenten frågade "Kan min dotter ringa mig?" och vill ha kontakt.',
                    "confidence": 0.9,
                    "parameters": {"relative_contact": "demo-relative"},
                },
            }
        ))
        event = make_sensor_event("door-open", event_id="family-contact-checkin")

        pending = demo.service.start(event)
        self.assertEqual(pending.outcome, WorkflowOutcome.AWAITING_RESIDENT)
        self.assertEqual(pending.proposal.action_type, "ask_resident")
        transcriber = Mock(transcribe=Mock(return_value=Transcript(
            "Kan min dotter ringa mig?", "orange-kb-whisper", "sv"
        )))
        result = self.resume(demo, event, pending.voice_prompt, transcriber)

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(result.proposal.action_type, "notify_relative")
        self.assertEqual(result.decision.status.value, "approve")
        self.assertEqual(result.receipt.status.value, "succeeded")
        self.assertEqual(demo.dashboard.records, ())
        self.assertEqual(len(demo.dashboard.action_records), 1)
        action_record = demo.dashboard.action_records[0]
        self.assertEqual(action_record.record_type, "relative_notification")
        self.assertEqual(action_record.subject_id, subject_id)

    def test_critical_first_pass_dismissal_is_denied(self):
        demo = create_streaming_demo()
        demo.service._triage = TriageAgent(DeterministicFakeChatModel(
            scripted_responses={"initial": {
                "action_type": "dismiss",
                "rationale": "The sensor reading is dismissed.",
                "confidence": 0.9,
                "parameters": {},
            }}
        ))

        result = demo.service.start(
            make_sensor_event("critical-cold", event_id="critical-first-pass-dismiss")
        )

        self.assertEqual(result.outcome, WorkflowOutcome.DENIED)
        self.assertEqual(
            result.decision.reason_codes,
            ("critical_dismissal_requires_resident_reply",),
        )
        self.assertIsNone(result.receipt)
        self.assertEqual(demo.dashboard.records, ())

    def test_critical_deviation_bypasses_debounce_on_first_reading(self):
        demo = create_streaming_demo()

        result = demo.service.start(make_sensor_event("critical-cold", event_id="critical-debounce-bypass"))

        self.assertEqual(result.assessment.severity, AssessmentSeverity.CRITICAL)
        self.assertEqual(result.outcome, WorkflowOutcome.AWAITING_RESIDENT)

    def test_active_anomaly_is_latched_until_normal_reading_resets_it(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="latch-first")
        event.observations["temperature_c"] = 21.0
        repeated = replace(
            event,
            event_id="latch-second",
            occurred_at=event.occurred_at + timedelta(seconds=30),
        )
        duplicate = replace(
            event,
            event_id="latch-third",
            occurred_at=event.occurred_at + timedelta(seconds=60),
        )
        normal = replace(
            event,
            event_id="latch-normal",
            occurred_at=event.occurred_at + timedelta(seconds=90),
            observations={
                "temperature_c": 18.0,
                "humidity_pct": 45.0,
                "door_state": "closed",
                "motion_detected": True,
            },
        )
        next_streak = replace(
            event,
            event_id="latch-after-reset",
            occurred_at=event.occurred_at + timedelta(seconds=120),
        )

        demo.service.start(event)
        self.assertEqual(demo.service.start(repeated).outcome, WorkflowOutcome.AWAITING_RESIDENT)
        suppressed = demo.service.start(duplicate)
        self.assertEqual(suppressed.outcome, WorkflowOutcome.DROPPED)
        self.assertEqual(suppressed.assessment.drop_reason, "persistent_anomaly_already_in_progress")
        self.assertEqual(demo.service.start(normal).outcome, WorkflowOutcome.NO_ACTION)
        self.assertEqual(demo.service.start(next_streak).outcome, WorkflowOutcome.DROPPED)

    def test_anomaly_streaks_are_isolated_by_resident(self):
        baselines = {
            DEFAULT_SUBJECT_ID: dict(DEFAULT_BASELINE),
            "synthetic-resident-2": dict(DEFAULT_BASELINE),
        }
        demo = create_streaming_demo(baselines=baselines)
        first = make_sensor_event("normal", event_id="resident-one-first")
        first.observations["temperature_c"] = 21.0
        second_resident = replace(
            first,
            event_id="resident-two-first",
            subject_id="synthetic-resident-2",
        )

        self.assertEqual(demo.service.start(first).outcome, WorkflowOutcome.DROPPED)
        self.assertEqual(demo.service.start(second_resident).outcome, WorkflowOutcome.DROPPED)

    def test_duplicate_event_id_is_processed_once(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="event-duplicate")

        first = demo.service.handle(event)
        second = demo.service.handle(event)

        self.assertIs(first, second)
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_missing_sensor_field_routes_to_maintenance_ticket_before_supervisor(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="event-invalid")
        event.observations.pop("humidity_pct")

        result = demo.service.handle(event)

        self.assertEqual(result.outcome, WorkflowOutcome.SENSOR_FAULT)
        self.assertEqual(result.proposal.action_type, "maintenance_ticket")
        self.assertEqual(result.decision.status.value, "approve")
        self.assertEqual(demo.dashboard.records[0].action_type, "maintenance_ticket")

    def test_run_command_executes_offline_scenario(self):
        import contextlib
        import io
        from aura.streaming.demo import main

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--scenario", "critical-cold"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"outcome": "dispatched"', output)
        self.assertIn("Dashboard records created: 1", output)

    def test_publish_command_sends_terminal_sensor_readings(self):
        from aura.streaming.demo import main

        publishers = []

        class PublisherSpy:
            def __init__(self, settings):
                self.events = []
                publishers.append(self)

            def publish(self, event):
                self.events.append(event)

            def close(self):
                pass

        with patch("aura.streaming.demo.LavinMQSettings.from_environment", return_value=SimpleNamespace(queue="aura.sensor.events")), patch(
            "aura.streaming.demo.PikaLavinMQPublisher", PublisherSpy
        ):
            exit_code = main([
                "publish",
                "--temperature-c", "21.5",
                "--humidity-pct", "42",
                "--door-state", "open",
                "--motion-detected", "false",
                "--subject-id", "resident-7",
                "--source", "hallway-sensor",
                "--event-id", "reading-123",
            ])

        self.assertEqual(exit_code, 0)
        sent_event = publishers[0].events[0]
        self.assertEqual(sent_event.event_id, "reading-123")
        self.assertEqual(sent_event.source, "hallway-sensor")
        self.assertEqual(sent_event.subject_id, "resident-7")
        self.assertEqual(sent_event.observations, {
            "temperature_c": 21.5,
            "humidity_pct": 42.0,
            "door_state": "open",
            "motion_detected": False,
        })


if __name__ == "__main__":
    unittest.main()