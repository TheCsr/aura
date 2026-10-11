"""Application decorators that enforce sensor validation at I/O boundaries."""

from typing import Optional

from ..contracts import InputEvent
from ..messaging.ports import EventHandler, EventPublisher
from .contracts import SensorReadings
from .validation import SensorEventValidator


class ValidatedSensorEventPublisher:
    """Validate sensor observations before delegating to a message publisher."""

    def __init__(
        self,
        publisher: EventPublisher,
        validator: Optional[SensorEventValidator] = None,
    ) -> None:
        self._publisher = publisher
        self._validator = validator or SensorEventValidator()

    def publish(self, event: InputEvent) -> None:
        self._validator.validate(event)
        self._publisher.publish(event)


class ValidatedSensorEventHandler:
    """Validate consumed sensor events before passing them to an application handler."""

    def __init__(
        self,
        handler: EventHandler,
        validator: Optional[SensorEventValidator] = None,
    ) -> None:
        self._handler = handler
        self._validator = validator or SensorEventValidator()

    def __call__(self, event: InputEvent) -> object:
        self._validator.validate(event)
        return self._handler(event)

    def validate_readings(self, event: InputEvent) -> SensorReadings:
        """Expose the typed readings to consumers that need parsed sensor values."""

        return self._validator.validate(event)