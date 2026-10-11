"""Validation from generic input events into the sensor domain contract."""

import math

from ..contracts import InputEvent
from .contracts import DoorState, SensorReadings


class SensorValidationError(ValueError):
    """Raised when an input event does not satisfy the sensor event contract."""


class SensorEventValidator:
    REQUIRED_FIELDS = frozenset(
        {"temperature_c", "humidity_pct", "door_state", "motion_detected"}
    )

    def __init__(
        self,
        *,
        temperature_min_c: float = -80.0,
        temperature_max_c: float = 100.0,
        allow_additional_observations: bool = False,
    ) -> None:
        for name, value in (
            ("temperature_min_c", temperature_min_c),
            ("temperature_max_c", temperature_max_c),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number")
        if temperature_min_c > temperature_max_c:
            raise ValueError("temperature_min_c must not exceed temperature_max_c")
        if not isinstance(allow_additional_observations, bool):
            raise ValueError("allow_additional_observations must be a boolean")
        self._temperature_min_c = temperature_min_c
        self._temperature_max_c = temperature_max_c
        self._allow_additional_observations = allow_additional_observations

    def validate(self, event: InputEvent) -> SensorReadings:
        observations = event.observations
        fields = frozenset(observations)
        missing = self.REQUIRED_FIELDS - fields
        if missing:
            raise SensorValidationError(
                "Missing required sensor observation(s): " + ", ".join(sorted(missing))
            )

        if not self._allow_additional_observations:
            unexpected = fields - self.REQUIRED_FIELDS
            if unexpected:
                raise SensorValidationError(
                    "Unexpected sensor observation(s): " + ", ".join(sorted(unexpected))
                )

        try:
            readings = SensorReadings(
                temperature_c=observations["temperature_c"],
                humidity_pct=observations["humidity_pct"],
                door_state=DoorState(observations["door_state"]),
                motion_detected=observations["motion_detected"],
            )
        except (TypeError, ValueError) as error:
            raise SensorValidationError(str(error)) from error

        if not self._temperature_min_c <= readings.temperature_c <= self._temperature_max_c:
            raise SensorValidationError(
                f"temperature_c must be between {self._temperature_min_c} "
                f"and {self._temperature_max_c} for this sensor configuration"
            )
        return readings