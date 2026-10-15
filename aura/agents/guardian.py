"""Deterministic Guardian policy gate and its validation subcomponents."""

from dataclasses import dataclass
import math
from typing import Mapping, Tuple

from ..contracts import (
    ActionProposal,
    Assessment,
    DecisionStatus,
    GuardianDecision,
    InputEvent,
)
from ..ports import ActionPolicy


@dataclass(frozen=True)
class ActionRule:
    minimum_assessment_confidence: float = 0.5
    minimum_proposal_confidence: float = 0.5
    allow_when_review_required: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_assessment_confidence", self.minimum_assessment_confidence),
            ("minimum_proposal_confidence", self.minimum_proposal_confidence),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        if not isinstance(self.allow_when_review_required, bool):
            raise ValueError("allow_when_review_required must be a boolean")


class ProposalEvidenceValidator:
    """Check event linkage and evidence references before evaluating policy."""

    def validate(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> Tuple[str, ...]:
        errors = []
        if assessment.event_id != event.event_id:
            errors.append("assessment_event_mismatch")
        if proposal.event_id != event.event_id:
            errors.append("proposal_event_mismatch")

        available_refs = {
            f"supervisor.evidence.{index}" for index in range(len(assessment.evidence))
        }
        if event.transcript is not None:
            available_refs.add("resident.reply.transcript")
        if not proposal.evidence_refs:
            errors.append("proposal_missing_evidence")
        elif len(set(proposal.evidence_refs)) != len(proposal.evidence_refs):
            errors.append("proposal_duplicate_evidence")
        elif any(reference not in available_refs for reference in proposal.evidence_refs):
            errors.append("proposal_unknown_evidence")
        return tuple(errors)


class GuardianDecisionValidator:
    """Ensure policy output is typed and tied to the proposal being evaluated."""

    def validate(
        self,
        event: InputEvent,
        proposal: ActionProposal,
        decision: GuardianDecision,
    ) -> GuardianDecision:
        if not isinstance(decision, GuardianDecision):
            return self._hold(event, proposal, "invalid_policy_result", "Policy returned an invalid result.")
        if decision.event_id != event.event_id or decision.proposal_id != proposal.proposal_id:
            return self._hold(
                event,
                proposal,
                "policy_result_mismatch",
                "Policy result did not match the event and proposal.",
            )
        return decision

    @staticmethod
    def _hold(
        event: InputEvent,
        proposal: ActionProposal,
        reason_code: str,
        rationale: str,
    ) -> GuardianDecision:
        return GuardianDecision(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DecisionStatus.HOLD,
            reason_codes=(reason_code,),
            rationale=rationale,
        )


class RuleBasedActionPolicy:
    """Approve only configured action types that satisfy deterministic rules."""

    def __init__(self, rules: Mapping[str, ActionRule] = None) -> None:
        configured_rules = (
            {"request_human_review": ActionRule(0.0, 0.0, allow_when_review_required=True)}
            if rules is None
            else dict(rules)
        )
        if any(not isinstance(key, str) or not key.strip() for key in configured_rules):
            raise ValueError("policy action types must be non-empty strings")
        if any(not isinstance(rule, ActionRule) for rule in configured_rules.values()):
            raise ValueError("policy rules must be ActionRule instances")
        self._rules = configured_rules

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
                "event_binding_mismatch",
                "Assessment and proposal must belong to the input event.",
            )

        rule = self._rules.get(proposal.action_type)
        if rule is None:
            return self._decision(
                event,
                proposal,
                DecisionStatus.DENY,
                "unsupported_action_type",
                "This action type is not enabled by Guardian policy.",
            )
        if not assessment.is_anomaly and not assessment.requires_review:
            return self._decision(
                event,
                proposal,
                DecisionStatus.DENY,
                "assessment_not_actionable",
                "No anomaly or review requirement supports this proposal.",
            )
        if assessment.requires_review and not rule.allow_when_review_required:
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                "review_required",
                "The assessment requires additional human review before this action.",
            )
        if (
            assessment.confidence < rule.minimum_assessment_confidence
            or proposal.confidence < rule.minimum_proposal_confidence
        ):
            return self._decision(
                event,
                proposal,
                DecisionStatus.HOLD,
                "confidence_below_policy_threshold",
                "Assessment or proposal confidence is below the configured threshold.",
            )
        return self._decision(
            event,
            proposal,
            DecisionStatus.APPROVE,
            "policy_requirements_met",
            "The proposal satisfies the configured deterministic policy.",
        )

    @staticmethod
    def _decision(
        event: InputEvent,
        proposal: ActionProposal,
        status: DecisionStatus,
        reason_code: str,
        rationale: str,
    ) -> GuardianDecision:
        return GuardianDecision(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=status,
            reason_codes=(reason_code,),
            rationale=rationale,
        )


class GuardianAgent:
    """Validate a proposal, apply deterministic policy, and validate the decision."""

    def __init__(
        self,
        policy: ActionPolicy = None,
        *,
        proposal_validator: ProposalEvidenceValidator = None,
        decision_validator: GuardianDecisionValidator = None,
    ) -> None:
        self._policy = policy or RuleBasedActionPolicy()
        self._proposal_validator = proposal_validator or ProposalEvidenceValidator()
        self._decision_validator = decision_validator or GuardianDecisionValidator()

    def run(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> GuardianDecision:
        errors = self._proposal_validator.validate(event, assessment, proposal)
        if errors:
            return GuardianDecision(
                event_id=event.event_id,
                proposal_id=proposal.proposal_id,
                status=DecisionStatus.HOLD,
                reason_codes=errors,
                rationale="Proposal or supporting evidence failed Guardian validation.",
            )
        decision = self._policy.evaluate(event, assessment, proposal)
        return self._decision_validator.validate(event, proposal, decision)

    def __call__(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> GuardianDecision:
        return self.run(event, assessment, proposal)