"""Configurable sensor-quality checks for event streams."""

from dataclasses import dataclass
import json
import math
import os
from typing import Dict, Mapping, Optional, Tuple

from ..contracts import InputEvent, JsonValue
from .validation import SensorEventValidator


@dataclass(frozen=True)
class SensorQualityFinding:
    code: str
    field: str
    description: str
    details: Mapping[str, JsonValue]


class SensorQualityMonitor:
    """Detect malformed, implausible, and configured stuck sensor readings."""

    _RANGES = {
        "temperature_c": (-80.0, 100.0),
        "humidity_pct": (0.0, 100.0),
    }

    def __init__(
        self,
        *,
        max_change_per_second: Optional[Mapping[str, float]] = None,
        repeated_value_limit: Optional[Mapping[str, int]] = None,
    ) -> None:
        self._max_change_per_second = dict(max_change_per_second or {})
        for field, limit in self._max_change_per_second.items():
            if (
                not isinstance(field, str)
                or not field.strip()
                or isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not math.isfinite(limit)
                or limit <= 0
            ):
                raise ValueError("max_change_per_second must contain positive finite limits")

        self._repeated_value_limit = dict(repeated_value_limit or {})
        for field, limit in self._repeated_value_limit.items():
            if (
                not isinstance(field, str)
                or not field.strip()
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 2
            ):
                raise ValueError("repeated_value_limit must contain integer limits of at least 2")

        self._previous: Dict[Tuple[str, str], Tuple[object, Mapping[str, JsonValue]]] = {}
        self._repeat_counts: Dict[Tuple[str, str, str], int] = {}
        self._seen_events: Dict[Tuple[str, str], Tuple[SensorQualityFinding, ...]] = {}

    @classmethod
    def from_environment(cls) -> "SensorQualityMonitor":
        def read_mapping(name: str) -> Mapping[str, object]:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return {}
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"{name} must be a JSON object") from error
            if not isinstance(value, dict):
                raise ValueError(f"{name} must be a JSON object")
            return value

        return cls(
            max_change_per_second=read_mapping("AURA_SENSOR_MAX_CHANGE_PER_SECOND"),
            repeated_value_limit=read_mapping("AURA_SENSOR_REPEATED_VALUE_LIMITS"),
        )

    def inspect(self, event: InputEvent) -> Tuple[SensorQualityFinding, ...]:
        event_key = (event.subject_id, event.event_id)
        if event_key in self._seen_events:
            return self._seen_events[event_key]

        findings = []
        observations = event.observations
        missing = SensorEventValidator.REQUIRED_FIELDS - set(observations)
        for field in sorted(missing):
            findings.append(SensorQualityFinding(
                "missing_field", field, f"Required sensor field '{field}' is missing.",
                {"field": field},
            ))

        valid_values: Dict[str, JsonValue] = {}
        for field, value in observations.items():
            if field in self._RANGES:
                lower, upper = self._RANGES[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not lower <= value <= upper
                ):
                    findings.append(SensorQualityFinding(
                        "impossible_reading", field,
                        f"Sensor field '{field}' is outside its supported physical range.",
                        {"field": field, "observed": value},
                    ))
                    continue
            elif field == "door_state" and value not in {"open", "closed", "unknown"}:
                findings.append(SensorQualityFinding(
                    "impossible_reading", field,
                    "Door state is not a supported sensor value.",
                    {"field": field, "observed": value},
                ))
                continue
            elif field == "motion_detected" and not isinstance(value, bool):
                findings.append(SensorQualityFinding(
                    "impossible_reading", field,
                    "Motion state must be a boolean sensor value.",
                    {"field": field, "observed": value},
                ))
                continue
            valid_values[field] = value

        source_key = (event.subject_id, event.source)
        previous = self._previous.get(source_key)
        if previous is not None:
            previous_at, previous_values = previous
            elapsed = (event.occurred_at - previous_at).total_seconds()
            if elapsed > 0:
                for field, value in valid_values.items():
                    prior_value = previous_values.get(field)
                    max_rate = self._max_change_per_second.get(field)
                    if (
                        max_rate is not None
                        and type(value) in (int, float)
                        and type(prior_value) in (int, float)
                    ):
                        rate = abs(value - prior_value) / elapsed
                        if rate > max_rate:
                            findings.append(SensorQualityFinding(
                                "impossible_jump", field,
                                f"Sensor field '{field}' changed faster than its configured rate limit.",
                                {
                                    "field": field,
                                    "observed": value,
                                    "previous": prior_value,
                                    "elapsed_seconds": elapsed,
                                    "change_per_second": rate,
                                    "configured_max_change_per_second": max_rate,
                                },
                            ))

                    repeat_key = (event.subject_id, event.source, field)
                    repeat_limit = self._repeated_value_limit.get(field)
                    if repeat_limit is not None:
                        count = self._repeat_counts.get(repeat_key, 1) + 1 if value == prior_value else 1
                        self._repeat_counts[repeat_key] = count
                        if count == repeat_limit:
                            findings.append(SensorQualityFinding(
                                "repeated_identical_value", field,
                                f"Sensor field '{field}' repeated the same value {count} times.",
                                {"field": field, "value": value, "consecutive_readings": count},
                            ))
                    else:
                        self._repeat_counts.pop(repeat_key, None)

        self._previous[source_key] = (event.occurred_at, valid_values)
        result = tuple(findings)
        self._seen_events[event_key] = result
        return result