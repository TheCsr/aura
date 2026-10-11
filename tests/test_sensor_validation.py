import unittest
from datetime import datetime, timezone

from aura.contracts import InputEvent
from aura.sensors import (
    DoorState,
    SensorEventValidator,
    SensorValidationError,
    ValidatedSensorEventHandler,
    ValidatedSensorEventPublisher,
)


def make_event(observations=None):
    return InputEvent(
        event_id="sensor-event-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-resident-1",
        source="sensor-gateway",
        observations=(
            {
                "temperature_c": -30.0,
                "humidity_pct": 42.5,
                "door_state": "closed",
                "motion_detected": False,
            }
            if observations is None
            else observations
        ),
    )


class RecordingPublisher:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)


class SensorEventValidatorTests(unittest.TestCase):
    def test_parses_valid_readings_into_typed_contract(self):
        readings = SensorEventValidator().validate(make_event())

        self.assertEqual(readings.temperature_c, -30.0)
        self.assertEqual(readings.humidity_pct, 42.5)
        self.assertEqual(readings.door_state, DoorState.CLOSED)
        self.assertFalse(readings.motion_detected)

    def test_rejects_missing_and_unexpected_sensor_fields(self):
        with self.assertRaisesRegex(SensorValidationError, "Missing required"):
            SensorEventValidator().validate(
                make_event({"temperature_c": 20.0, "humidity_pct": 40.0})
            )
        with self.assertRaisesRegex(SensorValidationError, "Unexpected"):
            SensorEventValidator().validate(
                make_event(
                    {
                        "temperature_c": 20.0,
                        "humidity_pct": 40.0,
                        "door_state": "closed",
                        "motion_detected": False,
                        "unregistered": 1,
                    }
                )
            )

    def test_rejects_out_of_range_or_non_finite_measurements(self):
        valid = {
            "temperature_c": 20.0,
            "humidity_pct": 40.0,
            "door_state": "closed",
            "motion_detected": False,
        }
        for field, value in (
            ("temperature_c", -81.0),
            ("temperature_c", float("inf")),
            ("humidity_pct", 101.0),
            ("humidity_pct", float("nan")),
        ):
            with self.subTest(field=field, value=value):
                observations = dict(valid)
                event = make_event(observations)
                event.observations[field] = value
                with self.assertRaises(SensorValidationError):
                    SensorEventValidator().validate(event)

    def test_rejects_invalid_enum_and_boolean_types(self):
        valid = {
            "temperature_c": 20.0,
            "humidity_pct": 40.0,
            "door_state": "closed",
            "motion_detected": False,
        }
        for field, value in (("door_state", "ajar"), ("motion_detected", 1)):
            with self.subTest(field=field):
                observations = dict(valid)
                observations[field] = value
                with self.assertRaises(SensorValidationError):
                    SensorEventValidator().validate(make_event(observations))

    def test_supports_configured_temperature_bounds_and_additional_fields(self):
        validator = SensorEventValidator(
            temperature_min_c=-40.0,
            temperature_max_c=50.0,
            allow_additional_observations=True,
        )
        event = make_event(
            {
                "temperature_c": -30.0,
                "humidity_pct": 42.5,
                "door_state": "unknown",
                "motion_detected": True,
                "co2_ppm": 550,
            }
        )

        readings = validator.validate(event)

        self.assertEqual(readings.door_state, DoorState.UNKNOWN)
        with self.assertRaisesRegex(SensorValidationError, "sensor configuration"):
            SensorEventValidator(temperature_min_c=0.0, temperature_max_c=40.0).validate(
                make_event()
            )

    def test_rejects_invalid_configured_temperature_bounds(self):
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            SensorEventValidator(temperature_min_c=20.0, temperature_max_c=10.0)
        with self.assertRaisesRegex(ValueError, "must be a finite number"):
            SensorEventValidator(temperature_min_c=float("nan"))
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            SensorEventValidator(allow_additional_observations=1)


class SensorValidationBoundaryTests(unittest.TestCase):
    def test_invalid_event_is_not_published(self):
        publisher = RecordingPublisher()
        validated = ValidatedSensorEventPublisher(publisher)

        with self.assertRaises(SensorValidationError):
            validated.publish(make_event({"temperature_c": 20.0}))
        self.assertEqual(publisher.published, [])

    def test_valid_event_is_published(self):
        publisher = RecordingPublisher()
        event = make_event()

        ValidatedSensorEventPublisher(publisher).publish(event)

        self.assertEqual(publisher.published, [event])

    def test_invalid_consumed_event_does_not_reach_handler(self):
        handled = []
        handler = ValidatedSensorEventHandler(handled.append)

        with self.assertRaises(SensorValidationError):
            handler(make_event({"door_state": "open"}))
        self.assertEqual(handled, [])

    def test_valid_consumed_event_reaches_handler(self):
        handled = []
        event = make_event()

        ValidatedSensorEventHandler(handled.append)(event)

        self.assertEqual(handled, [event])


if __name__ == "__main__":
    unittest.main()