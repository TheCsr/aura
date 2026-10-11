"""Typed sensor readings independent of the transport or agent workflow."""

from dataclasses import dataclass
from enum import Enum
import math


class DoorState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SensorReadings:
    temperature_c: float
    humidity_pct: float
    door_state: DoorState
    motion_detected: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.temperature_c, bool)
            or not isinstance(self.temperature_c, (int, float))
            or not math.isfinite(self.temperature_c)
            or not -80.0 <= self.temperature_c <= 100.0
        ):
            raise ValueError("temperature_c must be finite and between -80 and 100")
        if (
            isinstance(self.humidity_pct, bool)
            or not isinstance(self.humidity_pct, (int, float))
            or not math.isfinite(self.humidity_pct)
            or not 0.0 <= self.humidity_pct <= 100.0
        ):
            raise ValueError("humidity_pct must be finite and between 0 and 100")
        if not isinstance(self.door_state, DoorState):
            try:
                object.__setattr__(self, "door_state", DoorState(self.door_state))
            except (TypeError, ValueError) as error:
                raise ValueError("door_state must be open, closed, or unknown") from error
        if not isinstance(self.motion_detected, bool):
            raise ValueError("motion_detected must be a boolean")

        object.__setattr__(self, "temperature_c", float(self.temperature_c))
        object.__setattr__(self, "humidity_pct", float(self.humidity_pct))