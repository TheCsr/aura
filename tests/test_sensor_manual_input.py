import unittest

import pika

from aura.messaging.codec import EventMessageCodec
from aura.sensors.manual_input import (
    DEFAULT_URL,
    build_sensor_event,
    manual_input_settings,
    publish_events,
)
from aura.sensors.validation import SensorValidationError


class FakeChannel:
    def __init__(self, fail_on_publish=False):
        self.is_open = True
        self.published = []
        self.fail_on_publish = fail_on_publish

    def exchange_declare(self, **kwargs):
        pass

    def queue_declare(self, **kwargs):
        pass

    def queue_bind(self, **kwargs):
        pass

    def confirm_delivery(self):
        pass

    def basic_publish(self, **kwargs):
        if self.fail_on_publish:
            raise pika.exceptions.StreamLostError("connection lost")
        self.published.append(kwargs)


class FakeConnection:
    def __init__(self, channel):
        self.channel_instance = channel
        self.is_open = True

    def channel(self):
        return self.channel_instance

    def close(self):
        self.is_open = False


class FakeConnectionFactory:
    def __init__(self, fail_on_publish=False):
        self.connections = []
        self.fail_on_publish = fail_on_publish

    def __call__(self, parameters):
        connection = FakeConnection(FakeChannel(self.fail_on_publish))
        self.connections.append(connection)
        return connection


def make_event(**overrides):
    values = {
        "subject_id": " resident-7 ",
        "temperature_c": 21,
        "humidity_pct": 40,
        "door_state": "open",
        "motion_detected": False,
    }
    values.update(overrides)
    return build_sensor_event(**values)


class ManualInputSettingsTests(unittest.TestCase):
    def test_defaults_to_local_broker_and_demo_topology(self):
        settings = manual_input_settings({})

        self.assertEqual(settings.url, DEFAULT_URL)
        self.assertEqual(settings.exchange, "aura.events")
        self.assertEqual(settings.queue, "aura.sensor.events")
        self.assertEqual(settings.routing_key, "sensor.event")

    def test_uses_environment_url_and_topology(self):
        settings = manual_input_settings(
            {"LAVINMQ_URL": "amqps://u:p@host.example/v", "LAVINMQ_QUEUE": "custom.queue"}
        )

        self.assertEqual(settings.url, "amqps://u:p@host.example/v")
        self.assertEqual(settings.queue, "custom.queue")


class BuildSensorEventTests(unittest.TestCase):
    def test_builds_event_with_normalised_observations(self):
        event = make_event()

        self.assertTrue(event.event_id.startswith("sensor-manual-"))
        self.assertEqual(event.subject_id, "resident-7")
        self.assertIsNotNone(event.occurred_at.utcoffset())
        self.assertEqual(
            dict(event.observations),
            {"temperature_c": 21.0, "humidity_pct": 40.0, "door_state": "open", "motion_detected": False},
        )
        self.assertNotEqual(event.event_id, make_event().event_id)

    def test_blank_resident_id_is_rejected(self):
        with self.assertRaises(ValueError):
            make_event(subject_id="   ")


class PublishEventsTests(unittest.TestCase):
    def test_publishes_events_in_order_and_closes_connection(self):
        factory = FakeConnectionFactory()
        events = [make_event(), make_event(temperature_c=-20.0)]

        published = publish_events(manual_input_settings({}), events, connection_factory=factory)

        sent = factory.connections[0].channel_instance.published
        self.assertEqual(published, [event.event_id for event in events])
        self.assertEqual([call["properties"].message_id for call in sent], published)
        self.assertEqual(EventMessageCodec.decode(sent[1]["body"]).observations["temperature_c"], -20.0)
        self.assertEqual(sent[0]["routing_key"], "sensor.event")
        self.assertFalse(factory.connections[0].is_open)

    def test_validation_blocks_out_of_range_reading_before_publishing(self):
        factory = FakeConnectionFactory()

        with self.assertRaises(SensorValidationError):
            publish_events(
                manual_input_settings({}),
                [make_event(temperature_c=500.0)],
                connection_factory=factory,
            )

        self.assertTrue(
            all(not connection.channel_instance.published for connection in factory.connections)
        )

    def test_validation_can_be_skipped_to_send_invalid_reading(self):
        factory = FakeConnectionFactory()

        publish_events(
            manual_input_settings({}),
            [make_event(temperature_c=500.0)],
            validate=False,
            connection_factory=factory,
        )

        body = factory.connections[0].channel_instance.published[0]["body"]
        self.assertEqual(EventMessageCodec.decode(body).observations["temperature_c"], 500.0)

    def test_broker_error_propagates_and_connection_is_closed(self):
        factory = FakeConnectionFactory(fail_on_publish=True)

        with self.assertRaises(pika.exceptions.AMQPError):
            publish_events(manual_input_settings({}), [make_event()], connection_factory=factory)

        self.assertFalse(factory.connections[0].is_open)


if __name__ == "__main__":
    unittest.main()
