"""In-process consumer and automatic end-to-end test for the sensor input UI.

The consumer runs the full agent workflow on a background thread, so testing
needs no second terminal. It reads the same queue as
``python -m aura.streaming.demo consume``; run only one of them at a time, or
the two compete for messages.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Callable, Deque, List, Mapping, Optional, Sequence, Tuple

import pika

from ..contracts import InputEvent, JsonValue
from ..messaging.adapters.lavinmq import LavinMQSettings, PikaLavinMQConsumer
from ..streaming.composition import StreamingDemo, create_streaming_demo
from .application import ValidatedSensorEventHandler
from .manual_input import PRESETS, build_sensor_event, publish_events
from .validation import SensorValidationError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessedReading:
    event_id: str
    subject_id: str
    received_at: datetime
    observations: Mapping[str, JsonValue]
    outcome: str
    guardian_status: Optional[str] = None
    reason_codes: Tuple[str, ...] = ()
    ticket_id: Optional[str] = None
    error: Optional[str] = None


class BackgroundConsumer:
    """Consume sensor events on a daemon thread and keep the latest results."""

    def __init__(
        self,
        settings: LavinMQSettings,
        *,
        connection_factory: Callable[[Any], Any] = pika.BlockingConnection,
        demo_factory: Callable[[], StreamingDemo] = create_streaming_demo,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_results: int = 200,
    ) -> None:
        self.settings = settings
        self._connection_factory = connection_factory
        self._demo = demo_factory()
        self._clock = clock
        self._lock = threading.Lock()
        self._results: Deque[ProcessedReading] = deque(maxlen=max_results)
        self._consumer: Optional[PikaLavinMQConsumer] = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def results(self) -> Tuple[ProcessedReading, ...]:
        """Newest first."""

        with self._lock:
            return tuple(reversed(self._results))

    def result_for(self, event_id: str) -> Optional[ProcessedReading]:
        with self._lock:
            return next((item for item in self._results if item.event_id == event_id), None)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            consumer = PikaLavinMQConsumer(
                self.settings, self.handle, connection_factory=self._connection_factory
            )
            thread = threading.Thread(
                target=self._run, args=(consumer,), name="aura-sensor-ui-consumer", daemon=True
            )
            self._consumer, self._thread, self._error = consumer, thread, None
        thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> None:
        with self._lock:
            consumer, thread = self._consumer, self._thread
            self._consumer = self._thread = None
        if consumer is not None:
            consumer.stop()
        if thread is not None:
            thread.join(timeout_seconds)

    def handle(self, event: InputEvent) -> object:
        """Run the agents on one event; raising makes the consumer reject it."""

        received_at = self._clock()
        try:
            result = ValidatedSensorEventHandler(self._demo.service.handle)(event)
        except Exception as error:
            self._record(
                ProcessedReading(
                    event_id=event.event_id,
                    subject_id=event.subject_id,
                    received_at=received_at,
                    observations=dict(event.observations),
                    outcome="rejected",
                    error=f"{type(error).__name__}: {error}",
                )
            )
            raise
        self._record(
            ProcessedReading(
                event_id=event.event_id,
                subject_id=event.subject_id,
                received_at=received_at,
                observations=dict(event.observations),
                outcome=result.outcome.value,
                guardian_status=result.decision.status.value if result.decision else None,
                reason_codes=tuple(result.decision.reason_codes) if result.decision else (),
                ticket_id=result.receipt.external_reference if result.receipt else None,
            )
        )
        return result

    def _record(self, reading: ProcessedReading) -> None:
        with self._lock:
            self._results.append(reading)

    def _run(self, consumer: PikaLavinMQConsumer) -> None:
        try:
            consumer.run_forever(reconnect_delay_seconds=2.0)
        except Exception as error:
            logger.exception("Sensor UI consumer stopped unexpectedly")
            with self._lock:
                self._error = f"{type(error).__name__}: {error}"


# -- automatic test -------------------------------------------------------------

SELF_TEST_CASES: Tuple[Tuple[str, str], ...] = (
    ("Normal", "no_action"),
    ("Door open, no motion", "dispatched"),
    ("Critical cold", "dispatched"),
    ("Hot and humid", "dispatched"),
)
INVALID_READING_CASE = "Invalid reading (500 °C)"
BLOCKED = "blocked before publishing"


@dataclass(frozen=True)
class SelfTestResult:
    name: str
    expected: str
    actual: Optional[str]
    event_id: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


def run_self_test(
    settings: LavinMQSettings,
    consumer: BackgroundConsumer,
    *,
    subject_id: str,
    timeout_seconds: float = 20.0,
    publish: Callable[..., List[str]] = publish_events,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> List[SelfTestResult]:
    """Publish each preset through LavinMQ and compare the agents' outcomes.

    Raises the publisher's broker error if LavinMQ cannot be reached.
    """

    results: List[SelfTestResult] = []

    invalid = build_sensor_event(subject_id=subject_id, **{**PRESETS["Normal"], "temperature_c": 500.0})
    try:
        publish(settings, [invalid], validate=True)
        results.append(SelfTestResult(INVALID_READING_CASE, BLOCKED, "published", invalid.event_id))
    except SensorValidationError:
        results.append(SelfTestResult(INVALID_READING_CASE, BLOCKED, BLOCKED))

    consumer.start()
    events: Sequence[Tuple[str, str, InputEvent]] = [
        (name, expected, build_sensor_event(subject_id=subject_id, **PRESETS[name]))
        for name, expected in SELF_TEST_CASES
    ]
    publish(settings, [event for _, _, event in events], validate=True)

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline and any(consumer.result_for(e.event_id) is None for _, _, e in events):
        sleep(0.2)

    for name, expected, event in events:
        reading = consumer.result_for(event.event_id)
        results.append(
            SelfTestResult(name, expected, reading.outcome if reading else None, event.event_id)
        )
    return results
