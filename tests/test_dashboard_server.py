import unittest
from types import SimpleNamespace
from unittest.mock import patch
import requests
from starlette.testclient import TestClient

from aura.dashboard import server as dashboard_server
from aura.dashboard.server import app
from aura.messaging.codec import EventMessageCodec
from aura.streaming.demo import make_sensor_event
from aura.streaming.composition import create_streaming_demo
from aura.streaming.service import StreamingResult
from aura.streaming.voice import SyntheticSignalMicrophone
from aura.contracts import ActionProposal, Transcript
from aura.contracts import Assessment, AssessmentSeverity
from aura.graph import WorkflowOutcome
from aura.voice.adapters.orange_kb_whisper import OrangeKbWhisperAdapter


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.demo = create_streaming_demo()
        self.demo.service._critical_voice_timeout_seconds = 120.0
        self.demo.service._moderate_voice_timeout_seconds = 600.0
        self.demo_patch = patch.object(dashboard_server, "_demo", self.demo)
        self.demo_patch.start()
        self.addCleanup(self.demo_patch.stop)
        self.state_patch = patch.multiple(
            dashboard_server, _processed_results=[], _event_sensor_data={},
            _tickets_meta={}, _latest_result=None, _ticket_feedback={},
            _baseline_suggestions={}, _buddy_requests=[],
        )
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_dashboard_lifecycle_starts_and_stops_broker_consumer_thread(self):
        consumer = unittest.mock.Mock()
        consumer_thread = unittest.mock.Mock()
        sweeper_thread = unittest.mock.Mock()
        settings = SimpleNamespace()

        with patch.object(dashboard_server.LavinMQSettings, "from_environment", return_value=settings), patch.object(
            dashboard_server, "PikaLavinMQConsumer", return_value=consumer
        ) as consumer_factory, patch.object(
            dashboard_server, "Thread", side_effect=(consumer_thread, sweeper_thread)
        ) as thread_factory:
            with TestClient(app):
                consumer_factory.assert_called_once()
                self.assertEqual(thread_factory.call_count, 2)
                thread_factory.assert_any_call(
                    target=consumer.run_forever,
                    name="aura-lavinmq-consumer",
                    daemon=True,
                )
                thread_factory.assert_any_call(
                    target=dashboard_server._run_expiry_sweeper,
                    name="aura-reply-deadline-sweeper",
                    daemon=True,
                )
                consumer_thread.start.assert_called_once()
                sweeper_thread.start.assert_called_once()

        consumer.stop.assert_called_once()
        consumer_thread.join.assert_called_once_with(timeout=5.0)
        sweeper_thread.join.assert_called_once_with(timeout=2.0)

    def test_dashboard_launcher_uses_one_worker_process(self):
        with patch("uvicorn.run") as run:
            dashboard_server.run_server(host="127.0.0.1", port=8123)

        run.assert_called_once_with(
            "aura.dashboard.server:app",
            host="127.0.0.1",
            port=8123,
            reload=False,
            workers=1,
        )

    def test_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("broker_configured", data)
        self.assertIn("total_events_processed", data)
        self.assertIn("total_tickets", data)

    def test_metrics_endpoint_counts_dismissals_followups_and_estimated_visits_avoided(self):
        critical_event = make_sensor_event("critical-cold", event_id="metrics-critical-dismissed")
        critical_assessment = Assessment(
            event_id=critical_event.event_id,
            is_anomaly=True,
            summary="Critical synthetic deviation dismissed after review.",
            confidence=0.9,
            severity=AssessmentSeverity.CRITICAL,
        )
        dismissed_proposal = ActionProposal(
            proposal_id="metrics-dismiss-proposal",
            event_id=critical_event.event_id,
            action_type="dismiss",
            rationale="Synthetic reviewed dismissal.",
            confidence=0.9,
        )
        remote_event = make_sensor_event("door-open", event_id="metrics-remote-followup")
        remote_proposal = ActionProposal(
            proposal_id="metrics-remote-proposal",
            event_id=remote_event.event_id,
            action_type="remote_followup",
            rationale="Synthetic remote follow-up.",
            confidence=0.9,
        )
        self.demo.dashboard.execute(remote_event, remote_proposal)
        dashboard_server._processed_results.extend((
            StreamingResult(
                critical_event.event_id,
                WorkflowOutcome.DISMISSED,
                critical_assessment,
                proposal=dismissed_proposal,
            ),
            StreamingResult(
                remote_event.event_id,
                WorkflowOutcome.DISPATCHED,
                Assessment(
                    event_id=remote_event.event_id,
                    is_anomaly=True,
                    summary="Synthetic moderate deviation.",
                    confidence=0.9,
                    severity=AssessmentSeverity.MODERATE,
                ),
                proposal=remote_proposal,
            ),
        ))

        metrics = self.client.get("/api/metrics").json()

        self.assertEqual(metrics["dismissed"], 1)
        self.assertEqual(metrics["dismissed_critical_review"], 1)
        self.assertEqual(metrics["remote_followups"], 1)
        self.assertEqual(metrics["estimated_visits_avoided"], 2)

    def test_caseworker_feedback_builds_human_applied_baseline_suggestion(self):
        for index in range(3):
            event = make_sensor_event("normal", event_id=f"feedback-event-{index}")
            event.observations["temperature_c"] = 21.0
            proposal = ActionProposal(
                proposal_id=f"feedback-proposal-{index}",
                event_id=event.event_id,
                action_type="remote_followup",
                rationale="Synthetic follow-up ticket.",
                confidence=0.9,
            )
            receipt = self.demo.dashboard.execute(event, proposal)
            record_id = receipt.external_reference
            dashboard_server._tickets_meta[record_id] = {
                "status": "resolved", "notes": "", "priority": "Normal",
            }
            response = self.client.post(
                f"/api/tickets/{record_id}/feedback",
                json={"label": "false_alarm", "note": f"Test feedback {index}"},
            )
            self.assertEqual(response.status_code, 200)

        suggestions = self.client.get("/api/baseline-suggestions").json()["suggestions"]
        temperature_suggestion = next(
            suggestion for suggestion in suggestions if suggestion["field"] == "temperature_c"
        )
        self.assertEqual(temperature_suggestion["status"], "pending_review")
        self.assertEqual(temperature_suggestion["false_alarm_count"], 3)
        self.assertEqual(temperature_suggestion["current_value"], 18.0)
        self.assertEqual(temperature_suggestion["suggested_value"], 21.0)
        self.assertEqual(self.demo.baseline_store.get_baseline("synthetic-resident-1")["temperature_c"], 18.0)

        metrics = self.client.get("/api/metrics").json()
        self.assertEqual(metrics["remote_followups"], 3)
        self.assertEqual(metrics["false_alarm_feedback"], 3)
        self.assertEqual(metrics["false_alarm_rate"], 1.0)

        applied = self.client.post(
            "/api/baseline-suggestions/synthetic-resident-1/temperature_c/apply"
        )
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()["baseline"]["temperature_c"], 21.0)
        self.assertEqual(
            self.demo.baseline_store.get_baseline("synthetic-resident-1")["temperature_c"],
            21.0,
        )

    def test_feedback_requires_resolved_ticket(self):
        event = make_sensor_event("normal", event_id="feedback-not-resolved")
        proposal = ActionProposal(
            proposal_id="feedback-not-resolved-proposal",
            event_id=event.event_id,
            action_type="home_visit",
            rationale="Synthetic follow-up.",
            confidence=0.9,
        )
        receipt = self.demo.dashboard.execute(event, proposal)

        response = self.client.post(
            f"/api/tickets/{receipt.external_reference}/feedback",
            json={"label": "justified", "note": "Not closed yet."},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/metrics").json()["feedback_total"], 0)

    def test_simulate_normal_endpoint(self):
        response = self.client.post("/api/simulate", json={"scenario": "normal"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["outcome"], "no_action")
        self.assertEqual(data["result"]["sensor_data"]["observations"]["temperature_c"], 18.0)
        self.assertTrue(data["result"]["sensor_data"]["is_mock"])

    def test_consume_endpoint_runs_queued_event_into_dashboard_state(self):
        event = make_sensor_event("normal", event_id="dashboard-queue-event")

        class FakeChannel:
            def __init__(self):
                self.acknowledged = []

            def basic_get(self, *, queue, auto_ack):
                self.queue = queue
                self.auto_ack = auto_ack
                return SimpleNamespace(delivery_tag=7), None, EventMessageCodec.encode(event)

            def basic_ack(self, *, delivery_tag):
                self.acknowledged.append(delivery_tag)

        channel = FakeChannel()

        class FakeConnection:
            def channel(self):
                return channel

            def close(self):
                pass

        settings = SimpleNamespace(
            url="amqps://guest:guest@localhost/vhost",
            queue="aura.sensor.events",
        )
        with patch("aura.dashboard.server.LavinMQSettings.from_environment", return_value=settings), patch(
            "pika.BlockingConnection", return_value=FakeConnection()
        ):
            response = self.client.post("/api/consume")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["consumed_event_id"], event.event_id)
        self.assertEqual(payload["result"]["outcome"], "no_action")
        self.assertEqual(channel.acknowledged, [7])
        event_response = self.client.get("/api/events").json()["events"]
        matching_event = next(item for item in event_response if item["event_id"] == event.event_id)
        self.assertEqual(matching_event["sensor_data"]["source"], event.source)

    def test_broker_handler_routes_missing_sensor_field_to_maintenance_ticket(self):
        event = make_sensor_event("normal", event_id="sensor-fault-broker-event")
        event.observations.pop("humidity_pct")

        result = dashboard_server._handle_broker_event(event)

        self.assertEqual(result.outcome.value, "sensor_fault")
        tickets = self.client.get("/api/tickets").json()["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["action_type"], "maintenance_ticket")
        self.assertEqual(tickets[0]["status"], "maintenance_required")

    def test_non_ticket_action_records_are_exposed_separately_from_tickets(self):
        event = make_sensor_event("normal", event_id="relative-action-record")
        proposal = ActionProposal(
            proposal_id="relative-proposal",
            event_id=event.event_id,
            action_type="notify_relative",
            rationale="Notify the configured demo contact.",
            confidence=0.9,
            parameters={"reason_code": "no_resident_reply"},
        )
        self.demo.dashboard.execute(event, proposal)

        response = self.client.get("/api/action-records")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["records"][0]["record_type"], "relative_notification")
        self.assertEqual(self.client.get("/api/tickets").json()["tickets"], [])

    def test_buddy_profiles_include_relationship_and_demo_task_nda_status(self):
        response = self.client.get("/api/buddies")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["demo_only"])
        self.assertEqual(
            [(buddy["name"], buddy["relationship"], buddy["nda_signed"])
             for buddy in payload["buddies"]],
            [("Demo Companion Anna", "Daughter", True),
             ("Demo Companion Erik", "Son", True)],
        )
        self.assertTrue(all(buddy["synthetic"] for buddy in payload["buddies"]))

    def test_buddy_request_is_recorded_locally_without_ticket_or_external_contact(self):
        buddies = self.client.get("/api/buddies").json()
        self.assertTrue(buddies["demo_only"])
        buddy = buddies["buddies"][0]
        self.assertTrue(buddy["synthetic"])

        response = self.client.post(
            f"/api/buddies/{buddy['buddy_id']}/request",
            json={"subject_id": "synthetic-resident-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["status"], "queued_demo")
        self.assertTrue(response.json()["demo_only"])
        self.assertEqual(self.client.get("/api/tickets").json()["tickets"], [])
        self.assertEqual(len(self.client.get("/api/buddy-requests").json()["requests"]), 1)

    def test_simulate_critical_endpoint_waits_for_resident(self):
        response = self.client.post("/api/simulate", json={"scenario": "critical-cold"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["outcome"], "awaiting_resident")
        self.assertIsNone(data["result"]["dashboard_record_id"])
        self.assertEqual(data["result"]["triage"]["action_type"], "ask_resident")
        self.assertEqual(data["result"]["guardian"]["status"], "approve")

        # Check tickets endpoint
        tickets_resp = self.client.get("/api/tickets")
        self.assertEqual(tickets_resp.status_code, 200)
        tickets = tickets_resp.json()["tickets"]
        self.assertEqual(tickets, [])

    def test_device_trigger_prompt_and_tts(self):
        # Trigger prompt on device
        with patch.object(self.demo.service, "_critical_voice_timeout_seconds", 120.0):
            resp = self.client.post("/api/device/trigger-prompt", json={"scenario": "critical-cold"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("prompt", data)
        self.assertEqual(data["prompt"]["status"], "pending_reply")
        self.assertEqual(data["prompt"]["reply_timeout_seconds"], 120)
        self.assertGreater(data["prompt"]["remaining_reply_seconds"], 0)
        self.assertLessEqual(data["prompt"]["remaining_reply_seconds"], 120)

        # Get device prompt
        prompt_resp = self.client.get("/api/device/prompt")
        self.assertEqual(prompt_resp.status_code, 200)
        self.assertIsNotNone(prompt_resp.json()["prompt"])

        # Get TTS audio
        with patch.object(dashboard_server._google_tts, "synthesize", return_value=b"mp3" * 100):
            tts_resp = self.client.get("/api/device/tts", params={"text": "Hej, hur mår du?"})
        self.assertEqual(tts_resp.status_code, 200)
        self.assertEqual(tts_resp.headers["content-type"], "audio/mpeg")
        self.assertGreater(len(tts_resp.content), 100)

        # Post response from device
        prompt = data["prompt"]
        recording = SyntheticSignalMicrophone().capture()
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = True
            whisper.transcribe.return_value = Transcript("Jag ramlade i köket och fryser.", "orange-kb-whisper", "sv")
            respond_resp = self.client.post(
                "/api/device/respond",
                data={key: prompt[key] for key in ("event_id", "subject_id", "conversation_id")},
                files={"audio_file": ("reply.wav", recording.audio, "audio/wav")},
            )
            whisper.transcribe.assert_called_once()
        self.assertEqual(respond_resp.status_code, 200)
        res_data = respond_resp.json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["transcript"], "Jag ramlade i köket och fryser.")
        self.assertEqual(res_data["outcome"], "dispatched")
        self.assertIsNotNone(res_data["ticket_id"])
        self.assertIsNone(self.client.get("/api/device/prompt").json()["prompt"])
        history = self.client.get("/api/events").json()["events"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["outcome"], "dispatched")

    def trigger_prompt(self):
        return self.client.post("/api/device/trigger-prompt", json={"scenario": "critical-cold"}).json()["prompt"]

    def test_playback_completion_is_correlated_and_does_not_dispatch(self):
        prompt = self.trigger_prompt()
        identity = {key: prompt[key] for key in ("event_id", "subject_id", "conversation_id")}
        for status in ("started", "blocked", "failed", "stopped", "completed"):
            response = self.client.post("/api/device/playback", json={**identity, "provider": "browser_speech", "status": status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["playback"]["delivered"], status == "completed")
        latest = self.client.get("/api/latest").json()["latest"]
        self.assertEqual(latest["outcome"], "awaiting_resident")
        self.assertEqual(latest["triage"]["action_type"], "ask_resident")
        self.assertEqual(self.demo.dashboard.records, ())
        repeated = self.client.post("/api/device/playback", json={**identity, "provider": "gtts", "status": "failed"})
        self.assertTrue(repeated.json()["playback"]["delivered"])

    def test_playback_rejects_wrong_resident_and_expired_conversation(self):
        with patch.object(self.demo.service, "_clock", return_value=0):
            prompt = self.trigger_prompt()
        identity = {key: prompt[key] for key in ("event_id", "subject_id", "conversation_id")}
        with patch.object(self.demo.service, "_clock", return_value=1):
            response = self.client.post("/api/device/playback", json={**identity, "subject_id": "other", "provider": "gtts", "status": "completed"})
            self.assertEqual(response.status_code, 409)
        with patch.object(self.demo.service, "_clock", return_value=301):
            response = self.client.post("/api/device/playback", json={**identity, "provider": "gtts", "status": "completed"})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.demo.dashboard.records), 1)
        self.assertEqual(self.demo.dashboard.records[0].action_type, "urgent_visit")

    def test_deadline_sweeper_records_one_no_reply_urgent_ticket(self):
        with patch.object(self.demo.service, "_clock", return_value=0):
            self.trigger_prompt()
        with patch.object(self.demo.service, "_clock", return_value=121):
            dashboard_server._sweep_expired_conversations()
            dashboard_server._sweep_expired_conversations()

        latest = self.client.get("/api/latest").json()["latest"]
        tickets = self.client.get("/api/tickets").json()["tickets"]
        self.assertEqual(latest["outcome"], "escalated_no_reply")
        self.assertEqual(latest["triage"]["action_type"], "urgent_visit")
        self.assertIn("no_resident_reply", latest["triage"]["parameters"]["reason_code"])
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["action_type"], "urgent_visit")
        self.assertIn("no_resident_reply", tickets[0]["summary"])

    def send_audio(self, prompt, audio=None):
        return self.client.post(
            "/api/device/respond",
            data={key: prompt[key] for key in ("event_id", "subject_id", "conversation_id")},
            files={"audio_file": ("reply.wav", audio or SyntheticSignalMicrophone().capture().audio, "audio/wav")},
        )

    def test_uploaded_audio_uses_real_whisper_adapter_once_before_triage(self):
        prompt = self.trigger_prompt()
        recording = SyntheticSignalMicrophone().capture()
        whisper = OrangeKbWhisperAdapter(
            api_key="test-key", base_url="https://speech.example/v1-proxy", model="KBLab/kb-whisper-large",
        )
        with patch.object(dashboard_server, "_kb_whisper", whisper), patch(
            "aura.voice.adapters.orange_kb_whisper.requests.post"
        ) as post:
            post.return_value.json.return_value = {"text": "Jag behöver hjälp med dörren."}
            first = self.send_audio(prompt, recording.audio)
            duplicate = self.send_audio(prompt, recording.audio)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://speech.example/v1-proxy/v1/audio/transcriptions")
        self.assertEqual(post.call_args.kwargs["files"]["file"], ("reply.wav", recording.audio, "audio/wav"))
        self.assertEqual(post.call_args.kwargs["data"], {
            "model": "KBLab/kb-whisper-large", "language": "sv", "response_format": "json",
        })
        self.assertEqual(first.json()["transcript"], "Jag behöver hjälp med dörren.")
        self.assertEqual(first.json()["stt_source"], "orange-kb-whisper")
        self.assertEqual(first.json()["event_id"], prompt["event_id"])
        self.assertEqual(first.json()["ticket_id"], duplicate.json()["ticket_id"])
        latest = self.client.get("/api/latest").json()["latest"]
        self.assertEqual(latest["resident_reply"]["transcript"], first.json()["transcript"])
        self.assertEqual(latest["resident_reply"]["conversation_id"], prompt["conversation_id"])
        self.assertIsNotNone(latest["triage"])
        self.assertEqual(latest["triage"]["triage_stage"], "post_reply")
        self.assertEqual(len(self.demo.dashboard.records), 1)

    def test_whisper_http_failure_keeps_original_event_pending_without_fallback(self):
        prompt = self.trigger_prompt()
        whisper = OrangeKbWhisperAdapter(api_key="test-key", base_url="https://speech.example")
        with patch.object(dashboard_server, "_kb_whisper", whisper), patch(
            "aura.voice.adapters.orange_kb_whisper.requests.post",
            side_effect=requests.Timeout("provider-private-details"),
        ) as post:
            response = self.send_audio(prompt)

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("provider-private-details", response.text)
        post.assert_called_once()
        latest = self.client.get("/api/latest").json()["latest"]
        self.assertEqual(latest["event_id"], prompt["event_id"])
        self.assertEqual(latest["outcome"], "awaiting_resident")
        self.assertIsNone(latest["resident_reply"])
        self.assertEqual(latest["triage"]["action_type"], "ask_resident")
        self.assertEqual(self.demo.dashboard.records, ())

    def test_moderate_checkin_uses_longer_reply_timeout(self):
        self.demo.service._moderate_voice_timeout_seconds = 600.0
        observations = {
            "temperature_c": 21.0,
            "humidity_pct": 45.0,
            "door_state": "closed",
            "motion_detected": True,
        }
        first = self.client.post("/api/device/trigger-prompt", json={"custom_observations": observations})
        second = self.client.post("/api/device/trigger-prompt", json={"custom_observations": observations})

        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.json()["prompt"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["prompt"]["reply_timeout_seconds"], 600)
        self.assertGreater(second.json()["prompt"]["remaining_reply_seconds"], 0)
        self.assertLessEqual(second.json()["prompt"]["remaining_reply_seconds"], 600)

    def test_device_reply_requires_audio_and_correlation(self):
        response = self.client.post("/api/device/respond", data={"text_response": "invented reply"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.demo.dashboard.records, ())

    def test_stt_failure_does_not_fabricate_reply_or_create_ticket(self):
        prompt = self.trigger_prompt()
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = True
            whisper.transcribe.side_effect = RuntimeError("private-provider-data")
            response = self.send_audio(prompt)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-data", response.text)
        self.assertEqual(self.client.get("/api/device/prompt").json()["prompt"]["conversation_id"], prompt["conversation_id"])
        self.assertEqual(self.client.get("/api/latest").json()["latest"]["outcome"], "awaiting_resident")
        self.assertEqual(self.demo.dashboard.records, ())

    def test_missing_whisper_configuration_keeps_prompt_pending(self):
        prompt = self.trigger_prompt()
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = False
            self.assertEqual(self.send_audio(prompt).status_code, 503)
            whisper.transcribe.assert_not_called()
        self.assertEqual(self.demo.dashboard.records, ())

    def test_cross_resident_reply_is_rejected_before_transcription(self):
        prompt = self.trigger_prompt()
        prompt["subject_id"] = "other-resident"
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = True
            self.assertEqual(self.send_audio(prompt).status_code, 409)
            whisper.transcribe.assert_not_called()
        self.assertIsNone(self.client.get("/api/device/prompt", params={"subject_id": "other-resident"}).json()["prompt"])

    def test_duplicate_audio_reply_returns_same_ticket_without_retranscribing(self):
        prompt = self.trigger_prompt()
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = True
            whisper.transcribe.return_value = Transcript("Hjälp mig.", "orange-kb-whisper", "sv")
            first = self.send_audio(prompt)
            second = self.send_audio(prompt)
            whisper.transcribe.assert_called_once()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["ticket_id"], first.json()["ticket_id"])
        self.assertEqual(len(self.demo.dashboard.records), 1)

    def test_multiple_prompts_escalate_once_when_their_deadlines_pass(self):
        with patch.object(self.demo.service, "_clock", return_value=0):
            first = self.trigger_prompt()
            self.trigger_prompt()
            self.assertEqual(self.client.get("/api/device/prompt").json()["prompt"]["event_id"], first["event_id"])
        with patch.object(self.demo.service, "_clock", return_value=301):
            self.assertIsNone(self.client.get("/api/device/prompt").json()["prompt"])
            self.assertEqual(self.client.get("/api/latest").json()["latest"]["outcome"], "escalated_no_reply")
        self.assertEqual(len(self.demo.dashboard.records), 2)
        self.assertTrue(all(record.action_type == "urgent_visit" for record in self.demo.dashboard.records))

    def test_recording_size_limit_and_invalid_wav_do_not_call_stt(self):
        prompt = self.trigger_prompt()
        with patch.object(dashboard_server, "_kb_whisper") as whisper:
            whisper.is_configured = True
            self.assertEqual(self.send_audio(prompt, b"x" * (5 * 1024 * 1024 + 1)).status_code, 413)
            self.assertEqual(self.send_audio(prompt, b"not-wav").status_code, 409)
            whisper.transcribe.assert_not_called()

    def test_static_html_served_from_dist(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<!doctype html>", response.text.lower())


if __name__ == "__main__":
    unittest.main()
