"""Digital Twin Supervisor component and its replaceable subcomponents."""

import math
from typing import Mapping, Optional, Protocol

from ..contracts import Assessment, EvidenceItem, InputEvent, JsonValue
from ..ports import BaselineStore


class AssessmentStrategy(Protocol):
    def assess(
        self,
        event: InputEvent,
        baseline: Optional[Mapping[str, JsonValue]],
    ) -> Assessment:
        """Assess an event against available context without taking action."""


class AssessmentValidator:
    """Validate that a strategy result belongs to the input event."""

    def validate(self, event: InputEvent, assessment: Assessment) -> Assessment:
        if not isinstance(assessment, Assessment):
            raise ValueError("assessment strategy must return an Assessment")
        if assessment.event_id != event.event_id:
            raise ValueError("assessment event_id must match the input event")
        return assessment


class BaselineComparisonStrategy:
    """Compare configured event fields with the subject's stored baseline."""

    def __init__(self, numeric_tolerances: Optional[Mapping[str, float]] = None) -> None:
        self._numeric_tolerances = dict(numeric_tolerances or {})
        for field, tolerance in self._numeric_tolerances.items():
            if not isinstance(field, str) or not field.strip():
                raise ValueError("numeric tolerance field names must be non-empty strings")
            if (
                isinstance(tolerance, bool)
                or not isinstance(tolerance, (int, float))
                or not math.isfinite(tolerance)
                or tolerance < 0
            ):
                raise ValueError("numeric tolerances must be finite, non-negative numbers")

    def assess(
        self,
        event: InputEvent,
        baseline: Optional[Mapping[str, JsonValue]],
    ) -> Assessment:
        if baseline is None:
            return Assessment(
                event_id=event.event_id,
                is_anomaly=False,
                summary="Baseline unavailable; comparison requires review.",
                confidence=0.0,
                evidence=(
                    EvidenceItem(
                        source="baseline_store",
                        description="No baseline is available for this subject.",
                    ),
                ),
                requires_review=True,
            )

        comparable_fields = [
            field for field in event.observations if field in baseline
        ]
        if not comparable_fields:
            return Assessment(
                event_id=event.event_id,
                is_anomaly=False,
                summary="No event observations match the available baseline; comparison requires review.",
                confidence=0.0,
                evidence=(
                    EvidenceItem(
                        source="baseline_comparison",
                        description="No comparable observation fields were found.",
                        details={
                            "event_fields": list(event.observations),
                            "baseline_fields": list(baseline),
                        },
                    ),
                ),
                requires_review=True,
            )

        differences = []
        for field in comparable_fields:
            observed = event.observations[field]
            expected = baseline[field]
            if self._differs(field, observed, expected):
                differences.append(
                    EvidenceItem(
                        source="baseline_comparison",
                        description=f"Observation '{field}' differs from its baseline.",
                        details={
                            "field": field,
                            "observed": observed,
                            "baseline": expected,
                            "tolerance": self._numeric_tolerances.get(field, 0.0),
                        },
                    )
                )

        if differences:
            return Assessment(
                event_id=event.event_id,
                is_anomaly=True,
                summary=f"{len(differences)} observation field(s) differ from baseline.",
                confidence=1.0,
                evidence=tuple(differences),
            )

        return Assessment(
            event_id=event.event_id,
            is_anomaly=False,
            summary="Comparable observations match the baseline.",
            confidence=1.0,
            evidence=(
                EvidenceItem(
                    source="baseline_comparison",
                    description="All comparable observation fields match the baseline.",
                    details={"compared_fields": comparable_fields},
                ),
            ),
        )

    def _differs(self, field: str, observed: JsonValue, expected: JsonValue) -> bool:
        if type(observed) in (int, float) and type(expected) in (int, float):
            tolerance = self._numeric_tolerances.get(field, 0.0)
            return abs(observed - expected) > tolerance
        return type(observed) is not type(expected) or observed != expected


class SupervisorAgent:
    """Load baseline context, assess an event, and validate the result."""

    def __init__(
        self,
        baseline_store: BaselineStore,
        strategy: Optional[AssessmentStrategy] = None,
        validator: Optional[AssessmentValidator] = None,
    ) -> None:
        self._baseline_store = baseline_store
        self._strategy = strategy or BaselineComparisonStrategy()
        self._validator = validator or AssessmentValidator()

    def run(self, event: InputEvent) -> Assessment:
        baseline = self._baseline_store.get_baseline(event.subject_id)
        assessment = self._strategy.assess(event, baseline)
        return self._validator.validate(event, assessment)

    def __call__(self, event: InputEvent) -> Assessment:
        return self.run(event)