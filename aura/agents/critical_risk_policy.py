"""Deterministic Guardian policy for critical environmental sensor conditions."""

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from ..contracts import (
    ActionProposal,
    Assessment,
    DecisionStatus,
    GuardianDecision,
    InputEvent,
)
from ..ports import ActionPolicy


@dataclass(frozen=True)
class CriticalRiskThresholds:
    critical_temperature_min_c: float = -10.0
    critical_temperature_max_c: float = 45.0
    critical_humidity_min_pct: float = 5.0
    critical_humidity_max_pct: float = 95.0
    minimum_assessment_confidence: float = 0.5
    minimum_proposal_confidence: float = 0.5
    review_when_door_open_without_motion: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("critical_temperature_min_c", self.critical_temperature_min_c),
            ("critical_temperature_max_c", self.critical_temperature_max_c),
            ("critical_humidity_min_pct", self.critical_humidity_min_pct),
            ("critical_humidity_max_pct", self.critical_humidity_max_pct),
            ("minimum_assessment_confidence", self.minimum_assessment_confidence),
            ("minimum_proposal_confidence", self.minimum_proposal_confidence),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number")
        if self.critical_temperature_min_c >= self.critical_temperature_max_c:
            raise ValueError("critical temperature minimum must be less than maximum")
        if not 0 <= self.critical_humidity_min_pct < self.critical_humidity_max_pct <= 100:
            raise ValueError("critical humidity thresholds must be ordered within 0 and 100")
        if not 0 <= self.minimum_assessment_confidence <= 1:
            raise ValueError("minimum_assessment_confidence must be between 0 and 1")
        if not 0 <= self.minimum_proposal_confidence <= 1:
            raise ValueError("minimum_proposal_confidence must be between 0 and 1")
        if not isinstance(self.review_when_door_open_without_motion, bool):
            raise ValueError("review_when_door_open_without_motion must be a boolean")


class CriticalRiskPolicy(ActionPolicy):
    """Approve only event-bound human-review requests supported by sensor rules.

    Thresholds are configurable demo policy, not clinically validated limits.
    Acoustic measurements are intentionally not interpreted as pain or used to
    authorize actions by this policy.
    """

    REQUIRED_SENSOR_FIELDS = frozenset(
        {"temperature_c", "humidity_pct", "door_state", "motion_detected"}
    )

    def __init__(self, thresholds: Optional[CriticalRiskThresholds] = None) -> None:
        self._thresholds = thresholds or CriticalRiskThresholds()

    def evaluate(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> GuardianDecision:
        if assessment.event_id != event.event_id or proposal.event_id != event.event_id:
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("event_binding_mismatch",),
                "Event, assessment, and proposal identifiers must match.",
            )
        if proposal.action_type != "request_human_review":
            return self._decision(
                event,
                proposal,
                DecisionStatus.DENY,
                ("unsupported_action_type",),
                "CriticalRiskPolicy only permits a request for human review.",
            )

        missing_fields = self.REQUIRED_SENSOR_FIELDS - set(event.observations)
        if missing_fields:
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("sensor_context_incomplete",),
                "Required sensor readings are missing; Guardian cannot evaluate risk.",
            )

        sensor_error = self._validate_sensor_values(event)
        if sensor_error:
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("sensor_context_invalid",),
                sensor_error,
            )

        if not assessment.is_anomaly and not assessment.requires_review:
            return self._decision(
                event,
                proposal,
                DecisionStatus.DENY,
                ("assessment_not_actionable",),
                "Supervisor found no anomaly or review requirement.",
            )
        if assessment.requires_review:
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                ("supervisor_review_required",),
                "Supervisor requires review; request for human review approved.",
            )
        if (
            assessment.confidence < self._thresholds.minimum_assessment_confidence
            or proposal.confidence < self._thresholds.minimum_proposal_confidence
        ):
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("confidence_below_policy_threshold",),
                "Assessment or proposal confidence is below Guardian's configured threshold.",
            )

        indicators = self._risk_indicators(event)
        if indicators:
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                tuple(indicators),
                "Configured sensor risk condition met; request for human review approved.",
            )
        return self._decision(
            event,
            proposal,
            DecisionStatus.HOLD,
            ("critical_threshold_not_met",),
            "An anomaly was reported, but no configured critical sensor condition was met.",
        )

    def _risk_indicators(self, event: InputEvent) -> Tuple[str, ...]:
        observations = event.observations
        indicators = []
        temperature = observations["temperature_c"]
        humidity = observations["humidity_pct"]

        if temperature <= self._thresholds.critical_temperature_min_c:
            indicators.append("critical_low_temperature")
        elif temperature >= self._thresholds.critical_temperature_max_c:
            indicators.append("critical_high_temperature")
        if humidity <= self._thresholds.critical_humidity_min_pct:
            indicators.append("critical_low_humidity")
        elif humidity >= self._thresholds.critical_humidity_max_pct:
            indicators.append("critical_high_humidity")
        if (
            self._thresholds.review_when_door_open_without_motion
            and observations["door_state"] == "open"
            and observations["motion_detected"] is False
        ):
            indicators.append("door_open_without_motion")
        return tuple(indicators)

    @staticmethod
    def _validate_sensor_values(event: InputEvent) -> Optional[str]:
        observations = event.observations
        temperature = observations["temperature_c"]
        humidity = observations["humidity_pct"]
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not -80.0 <= temperature <= 100.0
        ):
            return "Temperature reading is invalid; Guardian is holding for human review."
        if (
            isinstance(humidity, bool)
            or not isinstance(humidity, (int, float))
            or not math.isfinite(humidity)
            or not 0.0 <= humidity <= 100.0
        ):
            return "Humidity reading is invalid; Guardian is holding for human review."
        door_state = observations["door_state"]
        if not isinstance(door_state, str) or door_state not in {"open", "closed", "unknown"}:
            return "Door state is invalid; Guardian is holding for human review."
        if not isinstance(observations["motion_detected"], bool):
            return "Motion reading is invalid; Guardian is holding for human review."
        return None

    @staticmethod
    def _decision(
        event: InputEvent,
        proposal: ActionProposal,
        status: DecisionStatus,
        reason_codes: Tuple[str, ...],
        rationale: str,
    ) -> GuardianDecision:
        return GuardianDecision(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=status,
            reason_codes=reason_codes,
            rationale=rationale,
        )