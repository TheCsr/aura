"""Build and publish hand-entered sensor readings, for the sensor input UI."""

from datetime import datetime, timezone
import os
from typing import Any, Callable, List, Mapping, Optional, Sequence
from uuid import uuid4

import pika

from ..contracts import InputEvent
from ..messaging.adapters.lavinmq import LavinMQSettings, PikaLavinMQPublisher
from .application import ValidatedSensorEventPublisher


DEFAULT_URL = "amqp://guest:guest@localhost:5672/"


def manual_input_settings(environment: Optional[Mapping[str, str]] = None) -> LavinMQSettings:
    """Same topology as ``python -m aura.streaming.demo``, with a local default URL."""

    values = dict(os.environ if environment is None else environment)
    if not values.get("LAVINMQ_URL", "").strip():
        values["LAVINMQ_URL"] = DEFAULT_URL
    return LavinMQSettings.from_environment(values)


def build_sensor_event(
    *,
    subject_id: str,
    temperature_c: float,
    humidity_pct: float,
    door_state: str,
    motion_detected: bool,
    source: str = "manual-sensor-input",
) -> InputEvent:
    """Build one reading; range checks happen when it is published."""

    return InputEvent(
        event_id=f"sensor-manual-{uuid4().hex}",
        occurred_at=datetime.now(timezone.utc),
        subject_id=subject_id.strip(),
        source=source,
        observations={
            "temperature_c": float(temperature_c),
            "humidity_pct": float(humidity_pct),
            "door_state": door_state,
            "motion_detected": bool(motion_detected),
        },
    )


def publish_events(
    settings: LavinMQSettings,
    events: Sequence[InputEvent],
    *,
    validate: bool = True,
    connection_factory: Callable[[Any], Any] = pika.BlockingConnection,
) -> List[str]:
    """Publish events in order over one confirmed connection, then close it.

    With ``validate=False`` invalid readings reach the broker and the consumer
    rejects them instead, which is how dead-lettering can be tested.
    """

    publisher = PikaLavinMQPublisher(settings, connection_factory)
    target = ValidatedSensorEventPublisher(publisher) if validate else publisher
    published: List[str] = []
    try:
        for event in events:
            target.publish(event)
            published.append(event.event_id)
    finally:
        publisher.close()
    return published
