import tempfile
import threading
import unittest
from pathlib import Path

from aura.sensors.live_consumer import (
    BLOCKED,
    INVALID_READING_CASE,
    SELF_TEST_CASES,
    BackgroundConsumer,
    run_self_test,
)
from aura.sensors.manual_input import (
    PRESETS,
    build_sensor_event,
    load_env_file,
    manual_input_settings,
)
from aura.sensors.validation import SensorEventValidator, SensorValidationError
from aura.streaming.composition import DEFAULT_SUBJECT_ID


class BlockingChannel:
    def __init__(self):
        self.is_open = True
        self.calls = []
        self._stop = threading.Event()

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)

        return record

    def start_consuming(self):
        self.calls.append("start_consuming")
        self._stop.wait(5)

    def stop_consuming(self):
        self.calls.append("stop_consuming")
        self._stop.set()


class FakeConnection:
    def __init__(self, parameters):
        self.channel_instance = BlockingChannel()
        self.is_open = True

    def channel(self):
        return self.channel_instance

    def close(self):
        self.is_open = False

    def add_callback_threadsafe(self, callback):
        callback()


class ConnectionFactory:
    def __init__(self):
        self.connections = []

    def __call__(self, parameters):
        connection = FakeConnection(parameters)
        self.connections.append(connection)
        return connection


def make_consumer(factory=None):
    return BackgroundConsumer(manual_input_settings({}), connection_factory=factory or ConnectionFactory())


def preset_event(name, **overrides):
    return build_sensor_event(subject_id=DEFAULT_SUBJECT_ID, **{**PRESETS[name], **overrides})


class BackgroundConsumerTests(unittest.TestCase):
    def test_handle_records_agent_outcome_and_ticket(self):
        consumer = make_consumer()
        event = preset_event("Critical cold")

        consumer.handle(event)

        reading = consumer.result_for(event.event_id)
        self.assertEqual(reading.outcome, "dispatched")
        self.assertEqual(reading.guardian_status, "approve")
        self.assertIn("critical_low_temperature", reading.reason_codes)
        self.assertTrue(reading.ticket_id)

    def test_normal_reading_needs_no_action(self):
        consumer = make_consumer()
        event = preset_event("Normal")

        consumer.handle(event)

        self.assertEqual(consumer.result_for(event.event_id).outcome, "no_action")
        self.assertIsNone(consumer.result_for(event.event_id).ticket_id)

    def test_invalid_reading_is_recorded_as_rejected_and_reraised(self):
        consumer = make_consumer()
        event = preset_event("Normal", temperature_c=500.0)

        with self.assertRaises(SensorValidationError):
            consumer.handle(event)

        reading = consumer.results()[0]
        self.assertEqual(reading.outcome, "rejected")
        self.assertIn("temperature_c", reading.error)

    def test_results_are_newest_first(self):
        consumer = make_consumer()
        first, second = preset_event("Normal"), preset_event("Critical cold")
        consumer.handle(first)
        consumer.handle(second)

        self.assertEqual([r.event_id for r in consumer.results()], [second.event_id, first.event_id])

    def test_start_and_stop_background_thread(self):
        factory = ConnectionFactory()
        consumer = make_consumer(factory)

        consumer.start()
        consumer.start()
        for _ in range(100):
            if factory.connections and "start_consuming" in factory.connections[0].channel_instance.calls:
                break
            threading.Event().wait(0.01)
        self.assertTrue(consumer.running)
        self.assertEqual(len(factory.connections), 1)

        consumer.stop()

        self.assertFalse(consumer.running)
        self.assertIn("stop_consuming", factory.connections[0].channel_instance.calls)


class SelfTestTests(unittest.TestCase):
    def setUp(self):
        self.consumer = make_consumer()
        self.consumer.start = lambda: None

    def deliver_through_consumer(self, settings, events, validate=True):
        """Stand-in broker: validates like publish_events, then delivers to the consumer."""

        published = self._validate_only(events, validate)
        for event in events:
            self.consumer.handle(event)
        return published

    def test_all_checks_pass_when_agents_respond(self):
        results = run_self_test(
            manual_input_settings({}),
            self.consumer,
            subject_id=DEFAULT_SUBJECT_ID,
            publish=self.deliver_through_consumer,
        )

        self.assertEqual(
            [(r.name, r.actual) for r in results],
            [(INVALID_READING_CASE, BLOCKED)] + [(name, expected) for name, expected in SELF_TEST_CASES],
        )
        self.assertTrue(all(r.passed for r in results))

    def test_missing_results_time_out_as_failures(self):
        clock = iter(range(0, 1000, 5))

        results = run_self_test(
            manual_input_settings({}),
            self.consumer,
            subject_id=DEFAULT_SUBJECT_ID,
            timeout_seconds=20,
            publish=lambda settings, events, validate=True: self._validate_only(events, validate),
            sleep=lambda seconds: None,
            monotonic=lambda: next(clock),
        )

        self.assertTrue(results[0].passed)
        self.assertTrue(all(r.actual is None and not r.passed for r in results[1:]))

    @staticmethod
    def _validate_only(events, validate):
        for event in events:
            if validate:
                SensorEventValidator().validate(event)
        return [event.event_id for event in events]


class LoadEnvFileTests(unittest.TestCase):
    def test_reads_quoted_values_and_keeps_existing_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\n"
                "LAVINMQ_URL='amqps://u:p@host/v'\n"
                'export LAVINMQ_QUEUE="custom.queue"\n'
                "ALREADY_SET=from-file\n"
                "not a pair\n",
                encoding="utf-8",
            )
            environment = {"ALREADY_SET": "from-shell"}

            load_env_file(path, environment)

        self.assertEqual(environment["LAVINMQ_URL"], "amqps://u:p@host/v")
        self.assertEqual(environment["LAVINMQ_QUEUE"], "custom.queue")
        self.assertEqual(environment["ALREADY_SET"], "from-shell")

    def test_missing_file_is_ignored(self):
        environment = {}
        load_env_file(Path("/nonexistent/.env"), environment)
        self.assertEqual(environment, {})


if __name__ == "__main__":
    unittest.main()
