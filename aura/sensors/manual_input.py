"""Build and publish hand-entered sensor readings, for the sensor input UI."""

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Callable, List, Mapping, MutableMapping, Optional, Sequence
from uuid import uuid4

import pika

from ..contracts import InputEvent
from ..messaging.adapters.lavinmq import LavinMQSettings, PikaLavinMQPublisher
from .application import ValidatedSensorEventPublisher


DEFAULT_URL = "amqp://guest:guest@localhost:5672/"

PRESETS: Mapping[str, Mapping[str, Any]] = {
    "Normal": {"temperature_c": 18.0, "humidity_pct": 45.0, "door_state": "closed", "motion_detected": True},
    "Door open, no motion": {"temperature_c": 18.0, "humidity_pct": 45.0, "door_state": "open", "motion_detected": False},
    "Critical cold": {"temperature_c": -20.0, "humidity_pct": 45.0, "door_state": "open", "motion_detected": False},
    "Hot and humid": {"temperature_c": 46.0, "humidity_pct": 96.0, "door_state": "closed", "motion_detected": True},
}


def load_env_file(path: Path, environment: Optional[MutableMapping[str, str]] = None) -> None:
    """Load KEY=VALUE lines from a .env file; variables already set are kept."""

    target = os.environ if environment is None else environment
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key:
            target.setdefault(key, value)


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
