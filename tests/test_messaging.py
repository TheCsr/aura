import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pika

from aura.contracts import InputEvent, Transcript
from aura.composition import create_demo_system
from aura.messaging.adapters.lavinmq import (
    LavinMQSettings,
    PikaLavinMQConsumer,
    PikaLavinMQPublisher,
)
from aura.messaging.codec import EventMessageCodec, InvalidEventMessage
from aura.sensors import ValidatedSensorEventHandler


class FakeChannel:
    def __init__(self):
        self.is_open = True
        self.calls = []
        self.delivery_callback = None
        self.publish_result = True

    def exchange_declare(self, **kwargs):
        self.calls.append(("exchange_declare", kwargs))

    def queue_declare(self, **kwargs):
        self.calls.append(("queue_declare", kwargs))

    def queue_bind(self, **kwargs):
        self.calls.append(("queue_bind", kwargs))

    def confirm_delivery(self):
        self.calls.append(("confirm_delivery", {}))

    def basic_publish(self, **kwargs):
        self.calls.append(("basic_publish", kwargs))
        return self.publish_result

    def basic_qos(self, **kwargs):
        self.calls.append(("basic_qos", kwargs))

    def basic_consume(self, **kwargs):
        self.calls.append(("basic_consume", kwargs))
        self.delivery_callback = kwargs["on_message_callback"]

    def basic_ack(self, **kwargs):
        self.calls.append(("basic_ack", kwargs))

    def basic_nack(self, **kwargs):
        self.calls.append(("basic_nack", kwargs))

    def start_consuming(self):
        self.calls.append(("start_consuming", {}))

    def stop_consuming(self):
        self.calls.append(("stop_consuming", {}))


class FakeConnection:
    def __init__(self, parameters):
        self.parameters = parameters
        self.channel_instance = FakeChannel()
        self.is_open = True

    def channel(self):
        return self.channel_instance

    def close(self):
        self.is_open = False

    def add_callback_threadsafe(self, callback):
        callback()


class FakeConnectionFactory:
    def __init__(self):
        self.connections = []

    def __call__(self, parameters):
        connection = FakeConnection(parameters)
        self.connections.append(connection)
        return connection


def make_settings(**overrides):
    values = {
        "url": "amqp://guest:guest@localhost:5672/%2F",
        "exchange": "test.events",
        "queue": "test.sensor.events",
        "routing_key": "sensor.event",
        "prefetch_count": 4,
    }
    values.update(overrides)
    return LavinMQSettings(**values)


def make_event():
    return InputEvent(
        event_id="evt-1",
        occurred_at=datetime(2026, 10, 11, 9, 30, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="sensor-gateway",
        observations={
            "temperature_c": 19.5,
            "humidity_pct": 42.0,
            "door_state": "closed",
            "motion_detected": False,
        },
        transcript=Transcript(text="I am fine", source="mock-stt", language="en"),
    )


def get_call(channel, name):
    return next(kwargs for call_name, kwargs in channel.calls if call_name == name)


class EventMessageCodecTests(unittest.TestCase):
    def test_round_trip_preserves_event_and_transcript(self):
        event = make_event()

        decoded = EventMessageCodec.decode(EventMessageCodec.encode(event))

        self.assertEqual(decoded, event)

    def test_rejects_invalid_json_and_unsupported_version(self):
        with self.assertRaisesRegex(InvalidEventMessage, "UTF-8 JSON"):
            EventMessageCodec.decode(b"not-json")
        with self.assertRaisesRegex(InvalidEventMessage, "Unsupported event schema"):
            EventMessageCodec.decode(
                b'{"schema_version":"2.0","event_id":"evt-1",'
                b'"occurred_at":"2026-10-11T09:30:00+00:00",'
                b'"subject_id":"resident-1","source":"test",'
                b'"observations":{},"transcript":null}'
            )

    def test_rejects_naive_timestamp_and_non_json_values(self):
        with self.assertRaisesRegex(InvalidEventMessage, "failed validation"):
            EventMessageCodec.decode(
                b'{"schema_version":"1.0","event_id":"evt-1",'
                b'"occurred_at":"2026-10-11T09:30:00","subject_id":"resident-1",'
                b'"source":"test","observations":{},"transcript":null}'
            )


class LavinMQSettingsTests(unittest.TestCase):
    def test_loads_environment_values_without_exposing_password_in_repr(self):
        settings = LavinMQSettings.from_environment(
            {
                "LAVINMQ_URL": "amqps://user:secret@broker.example/vhost",
                "LAVINMQ_QUEUE": "sensor-events",
                "LAVINMQ_PREFETCH_COUNT": "3",
                "LAVINMQ_DEAD_LETTER_EXCHANGE": "aura.dead",
            }
        )

        self.assertEqual(settings.queue, "sensor-events")
        self.assertEqual(settings.prefetch_count, 3)
        self.assertEqual(settings.dead_letter_exchange, "aura.dead")
        self.assertEqual(settings.dead_letter_queue, None)
        self.assertEqual(settings.dead_letter_routing_key, "sensor.event.failed")
        self.assertNotIn("secret", repr(settings))

    def test_default_queue_is_versioned_for_dead_letter_topology_migration(self):
        settings = LavinMQSettings.from_environment({
            "LAVINMQ_URL": "amqps://user:secret@broker.example/vhost",
        })

        self.assertEqual(settings.queue, "aura.sensor.events.v2")

    def test_requires_valid_url_and_prefetch_count(self):
        with self.assertRaisesRegex(ValueError, "LAVINMQ_URL"):
            LavinMQSettings.from_environment({})
        with self.assertRaisesRegex(ValueError, "amqp:// or amqps://"):
            make_settings(url="http://localhost")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            make_settings(prefetch_count=0)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            make_settings(prefetch_count=1.5)


class PikaLavinMQPublisherTests(unittest.TestCase):
    def test_publishes_persistent_confirmed_json_with_event_id(self):
        factory = FakeConnectionFactory()
        publisher = PikaLavinMQPublisher(make_settings(), connection_factory=factory)
        event = make_event()

        publisher.publish(event)

        channel = factory.connections[0].channel_instance
        publish = get_call(channel, "basic_publish")
        self.assertEqual(publish["exchange"], "test.events")
        self.assertEqual(publish["routing_key"], "sensor.event")
        self.assertEqual(publish["body"], EventMessageCodec.encode(event))
        self.assertTrue(publish["mandatory"])
        self.assertEqual(publish["properties"].message_id, event.event_id)
        self.assertEqual(
            int(publish["properties"].delivery_mode),
            pika.DeliveryMode.Persistent.value,
        )
        self.assertIn(("confirm_delivery", {}), channel.calls)
        self.assertEqual(get_call(channel, "queue_declare")["durable"], True)

    def test_dead_letter_exchange_is_configured_on_queue(self):
        factory = FakeConnectionFactory()
        publisher = PikaLavinMQPublisher(
            make_settings(dead_letter_exchange="aura.dead", dead_letter_routing_key="bad.event"),
            connection_factory=factory,
        )

        publisher.connect()

        channel = factory.connections[0].channel_instance
        self.assertIn(
            ("exchange_declare", {
                "exchange": "aura.dead",
                "exchange_type": "direct",
                "durable": True,
            }),
            channel.calls,
        )
        self.assertIn(
            ("queue_declare", {"queue": "test.sensor.events.dead", "durable": True}),
            channel.calls,
        )
        self.assertIn(
            ("queue_bind", {
                "exchange": "aura.dead",
                "queue": "test.sensor.events.dead",
                "routing_key": "bad.event",
            }),
            channel.calls,
        )
        primary_queue_declare = next(
            kwargs
            for call_name, kwargs in channel.calls
            if call_name == "queue_declare" and kwargs.get("queue") == "test.sensor.events"
        )
        self.assertEqual(
            primary_queue_declare["arguments"],
            {
                "x-dead-letter-exchange": "aura.dead",
                "x-dead-letter-routing-key": "bad.event",
            },
        )

    def test_default_settings_configure_a_durable_dead_letter_route(self):
        settings = LavinMQSettings.from_environment({
            "LAVINMQ_URL": "amqps://user:secret@broker.example/vhost",
        })

        self.assertEqual(settings.dead_letter_exchange, "aura.events.dead")
        self.assertEqual(settings.dead_letter_queue, None)
        self.assertEqual(settings.dead_letter_routing_key, "sensor.event.failed")


class PikaLavinMQConsumerTests(unittest.TestCase):
    def setUp(self):
        self.factory = FakeConnectionFactory()
        self.handled = []
        self.consumer = PikaLavinMQConsumer(
            make_settings(),
            self.handled.append,
            connection_factory=self.factory,
        )
        self.consumer.connect()
        self.connection = self.factory.connections[0]
        self.channel = self.connection.channel_instance
        self.callback = self.channel.delivery_callback

    def deliver(self, body, message_id="evt-1", content_type="application/json"):
        self.callback(
            self.channel,
            SimpleNamespace(delivery_tag=7),
            SimpleNamespace(message_id=message_id, content_type=content_type),
            body,
        )

    def test_acks_only_after_handler_succeeds(self):
        event = make_event()

        self.deliver(EventMessageCodec.encode(event))

        self.assertEqual(self.handled, [event])
        self.assertIn(("basic_ack", {"delivery_tag": 7}), self.channel.calls)
        self.assertFalse(any(name == "basic_nack" for name, _ in self.channel.calls))
        self.assertEqual(get_call(self.channel, "basic_qos")["prefetch_count"], 4)
        self.assertFalse(get_call(self.channel, "basic_consume")["auto_ack"])

    def test_nacks_invalid_message_without_requeue(self):
        self.deliver(b"not-json")

        self.assertEqual(self.handled, [])
        self.assertIn(
            ("basic_nack", {"delivery_tag": 7, "requeue": False}),
            self.channel.calls,
        )

    def test_default_consumer_topology_binds_a_durable_dead_letter_queue(self):
        self.assertIn(
            ("exchange_declare", {
                "exchange": "aura.events.dead",
                "exchange_type": "direct",
                "durable": True,
            }),
            self.channel.calls,
        )
        self.assertIn(
            ("queue_declare", {"queue": "test.sensor.events.dead", "durable": True}),
            self.channel.calls,
        )
        self.assertIn(
            ("queue_bind", {
                "exchange": "aura.events.dead",
                "queue": "test.sensor.events.dead",
                "routing_key": "sensor.event.failed",
            }),
            self.channel.calls,
        )
        primary_queue = next(
            kwargs
            for call_name, kwargs in self.channel.calls
            if call_name == "queue_declare" and kwargs.get("queue") == "test.sensor.events"
        )
        self.assertEqual(
            primary_queue["arguments"],
            {
                "x-dead-letter-exchange": "aura.events.dead",
                "x-dead-letter-routing-key": "sensor.event.failed",
            },
        )

    def test_nacks_handler_failure_and_mismatched_message_id(self):
        def failing_handler(event):
            raise RuntimeError("transient internal details")

        consumer = PikaLavinMQConsumer(
            make_settings(),
            failing_handler,
            connection_factory=self.factory,
        )
        consumer.connect()
        channel = self.factory.connections[1].channel_instance
        callback = channel.delivery_callback
        with self.assertLogs("aura.messaging.adapters.lavinmq.pika_adapter", level="ERROR"):
            callback(
                channel,
                SimpleNamespace(delivery_tag=8),
                SimpleNamespace(message_id="evt-1", content_type="application/json"),
                EventMessageCodec.encode(make_event()),
            )
        self.assertIn(("basic_nack", {"delivery_tag": 8, "requeue": False}), channel.calls)

        self.deliver(EventMessageCodec.encode(make_event()), message_id="wrong-id")
        self.assertIn(("basic_nack", {"delivery_tag": 7, "requeue": False}), self.channel.calls)

    def test_valid_delivery_invokes_composed_agent_workflow(self):
        event = InputEvent(
            event_id="evt-through-broker",
            occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            subject_id="synthetic-1",
            source="sensor-gateway",
            observations={"door_state": "open"},
        )
        system = create_demo_system(
            baselines={"synthetic-1": {"door_state": "closed"}},
            proposal_id_factory=lambda: "proposal-through-broker",
            ticket_id_factory=lambda: "ticket-through-broker",
        )
        consumer = PikaLavinMQConsumer(
            make_settings(),
            system.run,
            connection_factory=self.factory,
        )
        consumer.connect()
        channel = self.factory.connections[1].channel_instance

        channel.delivery_callback(
            channel,
            SimpleNamespace(delivery_tag=9),
            SimpleNamespace(message_id=event.event_id, content_type="application/json"),
            EventMessageCodec.encode(event),
        )

        self.assertEqual(len(system.mock_ticket_tool.tickets), 1)
        self.assertIn(("basic_ack", {"delivery_tag": 9}), channel.calls)

    def test_invalid_sensor_delivery_is_nacked_before_workflow(self):
        event = InputEvent(
            event_id="evt-invalid-sensor",
            occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            subject_id="synthetic-1",
            source="sensor-gateway",
            observations={"temperature_c": 22.0},
        )
        system = create_demo_system()
        consumer = PikaLavinMQConsumer(
            make_settings(),
            ValidatedSensorEventHandler(system.run),
            connection_factory=self.factory,
        )
        consumer.connect()
        channel = self.factory.connections[1].channel_instance

        with self.assertLogs("aura.messaging.adapters.lavinmq.pika_adapter", level="ERROR"):
            channel.delivery_callback(
                channel,
                SimpleNamespace(delivery_tag=10),
                SimpleNamespace(message_id=event.event_id, content_type="application/json"),
                EventMessageCodec.encode(event),
            )

        self.assertIn(("basic_nack", {"delivery_tag": 10, "requeue": False}), channel.calls)
        self.assertEqual(system.mock_ticket_tool.tickets, ())


if __name__ == "__main__":
    unittest.main()