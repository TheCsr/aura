import json
import unittest

from aura.graph import WorkflowOutcome
from aura.messaging.codec import EventMessageCodec
from aura.streaming.composition import create_streaming_demo
from aura.streaming.demo import make_sensor_event, result_as_json


class StreamingDemoTests(unittest.TestCase):
    def test_normal_sensor_event_short_circuits_without_voice_or_dashboard(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="event-normal")

        result = demo.service.handle(event)

        self.assertEqual(result.outcome, WorkflowOutcome.NO_ACTION)
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
        self.assertEqual(result.decision.reason_codes, ("critical_low_temperature", "door_open_without_motion"))
        self.assertEqual(len(demo.dashboard.records), 1)
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

        result = demo.service.handle(event)

        self.assertEqual(result.outcome, WorkflowOutcome.HELD)
        self.assertIsNotNone(result.voice_reply)
        self.assertEqual(result.decision.reason_codes, ("critical_threshold_not_met",))
        self.assertEqual(demo.dashboard.records, ())

    def test_duplicate_event_id_is_processed_once(self):
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="event-duplicate")

        first = demo.service.handle(event)
        second = demo.service.handle(event)

        self.assertIs(first, second)
        self.assertEqual(len(demo.dashboard.records), 1)

    def test_invalid_sensor_event_is_rejected_before_supervisor(self):
        demo = create_streaming_demo()
        event = make_sensor_event("normal", event_id="event-invalid")
        event.observations.pop("humidity_pct")

        with self.assertRaisesRegex(ValueError, "Missing required sensor"):
            demo.service.handle(event)
        self.assertEqual(demo.dashboard.records, ())


if __name__ == "__main__":
    unittest.main()