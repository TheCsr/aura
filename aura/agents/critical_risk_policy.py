"""Deterministic Guardian policy for critical environmental sensor conditions."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping, Optional, Tuple

from ..contracts import (
    ActionType,
    ActionProposal,
    Assessment,
    AssessmentSeverity,
    DecisionStatus,
    GuardianDecision,
    InputEvent,
    TriageStage,
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

    def __init__(
        self,
        thresholds: Optional[CriticalRiskThresholds] = None,
        *,
        resident_contexts: Optional[Mapping[str, Mapping[str, object]]] = None,
    ) -> None:
        self._thresholds = thresholds or CriticalRiskThresholds()
        self._resident_contexts = {}
        for subject_id, context in (resident_contexts or {}).items():
            if not isinstance(subject_id, str) or not subject_id.strip():
                raise ValueError("resident context subject IDs must be non-empty strings")
            if not isinstance(context, Mapping):
                raise ValueError("resident contexts must be mappings")
            self._resident_contexts[subject_id] = dict(context)

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
        if proposal.action_type == ActionType.MAINTENANCE_TICKET.value:
            if (
                assessment.requires_review
                and assessment.evidence
                and all(item.source == "sensor_quality" for item in assessment.evidence)
            ):
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.APPROVE,
                    ("sensor_quality_maintenance",),
                    "Validated sensor-quality evidence supports a maintenance ticket, not a resident visit.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.DENY,
                ("maintenance_requires_sensor_quality_evidence",),
                "Maintenance tickets require a review-required sensor-quality assessment.",
            )
        if proposal.action_type in {
            ActionType.DISMISS.value,
            ActionType.ASK_RESIDENT.value,
            ActionType.REMOTE_FOLLOWUP.value,
            ActionType.HOME_VISIT.value,
            ActionType.URGENT_VISIT.value,
            ActionType.NOTIFY_RELATIVE.value,
            ActionType.ATTACH_TO_PLANNED_VISIT.value,
        }:
            return self._evaluate_ladder_action(event, assessment, proposal)
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
        resident_reply_distress = any(
            item.source == "resident_reply_escalation"
            and item.details.get("reason_code") == "resident_reply_distress"
            for item in assessment.evidence
        )
        if (
            resident_reply_distress
            and proposal.triage_stage is TriageStage.POST_REPLY
            and event.transcript is not None
        ):
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                ("resident_reply_distress",),
                "A post-reply distress reason code requires immediate human review.",
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

    def _evaluate_ladder_action(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> GuardianDecision:
        missing_fields = self.REQUIRED_SENSOR_FIELDS - set(event.observations)
        if missing_fields:
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("sensor_context_incomplete",),
                "Required sensor readings are missing; Guardian cannot evaluate this action.",
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

        indicators = self._risk_indicators(event)
        critical = assessment.severity is AssessmentSeverity.CRITICAL or bool(indicators)
        confidence_met = (
            assessment.confidence >= self._thresholds.minimum_assessment_confidence
            and proposal.confidence >= self._thresholds.minimum_proposal_confidence
        )

        if proposal.action_type == ActionType.DISMISS.value:
            if critical and not (
                proposal.triage_stage is TriageStage.POST_REPLY
                and event.transcript is not None
            ):
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.DENY,
                    ("critical_dismissal_requires_resident_reply",),
                    "A critical event cannot be dismissed without a post-reply proposal and a non-empty resident transcript.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                ("triage_dismissal_approved",),
                "Triage dismissed this event; the decision remains visible in event history.",
            )

        if proposal.action_type == ActionType.ASK_RESIDENT.value:
            if (
                proposal.triage_stage is TriageStage.INITIAL
                and assessment.severity in {AssessmentSeverity.MODERATE, AssessmentSeverity.CRITICAL}
            ):
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.APPROVE,
                    ("resident_checkin_approved",),
                    "A resident check-in is allowed for moderate or critical severity.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                ("resident_checkin_requires_initial_moderate_or_critical_severity",),
                "A resident check-in requires an initial-pass proposal and moderate or critical severity.",
            )

        if proposal.action_type in {
            ActionType.REMOTE_FOLLOWUP.value,
            ActionType.HOME_VISIT.value,
        }:
            if not confidence_met:
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("confidence_below_policy_threshold",),
                    "Assessment or proposal confidence is below Guardian's configured threshold.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                (f"{proposal.action_type}_approved",),
                f"The {proposal.action_type} proposal meets Guardian's confidence thresholds.",
            )

        if proposal.action_type == ActionType.URGENT_VISIT.value:
            if not critical:
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("urgent_visit_downgraded_to_home_visit",),
                    "Urgent visit is held and downgraded to a home_visit recommendation for caseworker review.",
                )
            if not confidence_met:
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("confidence_below_policy_threshold",),
                    "Critical evidence exists, but confidence is below Guardian's configured threshold.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                indicators or ("critical_severity",),
                "Critical severity or a configured critical sensor reason supports an urgent visit.",
            )

        if proposal.action_type == ActionType.NOTIFY_RELATIVE.value:
            if not self._has_relative_contact(event.subject_id):
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("relative_contact_unavailable",),
                    "No demo relative contact is configured for this resident.",
                )
            if not confidence_met:
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("confidence_below_policy_threshold",),
                    "Assessment or proposal confidence is below Guardian's configured threshold.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                ("relative_contact_available",),
                "A demo relative contact is configured for this resident.",
            )

        if proposal.action_type == ActionType.ATTACH_TO_PLANNED_VISIT.value:
            if not self._has_upcoming_visit(event):
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("planned_visit_unavailable",),
                    "No valid upcoming demo visit is configured for this resident.",
                )
            if not confidence_met:
                return self._decision(
                    event,
                    proposal,
                    DecisionStatus.HOLD,
                    ("confidence_below_policy_threshold",),
                    "Assessment or proposal confidence is below Guardian's configured threshold.",
                )
            return self._decision(
                event,
                proposal,
                DecisionStatus.APPROVE,
                ("planned_visit_available",),
                "A valid upcoming demo visit is configured for this resident.",
            )

        return self._decision(
            event,
            proposal,
            DecisionStatus.DENY,
            ("unsupported_action_type",),
            "This action type has no configured Guardian rule.",
        )

    def _has_relative_contact(self, subject_id: str) -> bool:
        contact = self._resident_contexts.get(subject_id, {}).get("relative_contact")
        if isinstance(contact, str):
            return bool(contact.strip())
        if isinstance(contact, Mapping):
            return any(
                isinstance(contact.get(field), str) and bool(contact[field].strip())
                for field in ("name", "phone", "email", "contact_id")
            )
        return False

    def _has_upcoming_visit(self, event: InputEvent) -> bool:
        value = self._resident_contexts.get(event.subject_id, {}).get("next_planned_visit")
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            scheduled_at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return False
        return scheduled_at.utcoffset() is not None and scheduled_at > event.occurred_at

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