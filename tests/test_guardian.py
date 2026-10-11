import unittest
from datetime import datetime, timezone

from aura.agents.guardian import (
    ActionRule,
    GuardianAgent,
    RuleBasedActionPolicy,
)
from aura.contracts import (
    ActionProposal,
    Assessment,
    DecisionStatus,
    EvidenceItem,
    GuardianDecision,
    InputEvent,
)


def make_event():
    return InputEvent(
        event_id="evt-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="test",
    )


def make_assessment(event, *, is_anomaly=True, requires_review=False, confidence=0.9):
    return Assessment(
        event_id=event.event_id,
        is_anomaly=is_anomaly,
        summary="Synthetic assessment",
        confidence=confidence,
        evidence=(
            EvidenceItem(
                source="supervisor",
                description="Synthetic evidence",
                details={"field": "value"},
            ),
        ),
        requires_review=requires_review,
    )


def make_proposal(event, *, action_type="request_human_review", confidence=0.8, refs=None):
    return ActionProposal(
        proposal_id="proposal-1",
        event_id=event.event_id,
        action_type=action_type,
        rationale="Synthetic proposal",
        confidence=confidence,
        evidence_refs=("supervisor.evidence.0",) if refs is None else refs,
    )


class GuardianAgentTests(unittest.TestCase):
    def test_review_proposal_is_approved_by_default_policy(self):
        event = make_event()
        assessment = make_assessment(event, is_anomaly=False, requires_review=True, confidence=0.0)
        proposal = make_proposal(event)

        decision = GuardianAgent()(event, assessment, proposal)

        self.assertEqual(decision.status, DecisionStatus.APPROVE)
        self.assertEqual(decision.event_id, event.event_id)
        self.assertEqual(decision.proposal_id, proposal.proposal_id)
        self.assertEqual(decision.reason_codes, ("policy_requirements_met",))

    def test_unknown_evidence_is_held_without_calling_policy(self):
        event = make_event()
        assessment = make_assessment(event)
        proposal = make_proposal(event, refs=("supervisor.evidence.99",))
        policy = RecordingPolicy()

        decision = GuardianAgent(policy)(event, assessment, proposal)

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertIn("proposal_unknown_evidence", decision.reason_codes)
        self.assertEqual(policy.calls, 0)

    def test_missing_or_duplicate_evidence_is_held(self):
        event = make_event()
        assessment = make_assessment(event)

        missing = GuardianAgent()(event, assessment, make_proposal(event, refs=()))
        duplicate = GuardianAgent()(
            event,
            assessment,
            make_proposal(event, refs=("supervisor.evidence.0", "supervisor.evidence.0")),
        )

        self.assertEqual(missing.status, DecisionStatus.HOLD)
        self.assertIn("proposal_missing_evidence", missing.reason_codes)
        self.assertEqual(duplicate.status, DecisionStatus.HOLD)
        self.assertIn("proposal_duplicate_evidence", duplicate.reason_codes)

    def test_unsupported_action_is_denied(self):
        event = make_event()
        decision = GuardianAgent()(
            event,
            make_assessment(event),
            make_proposal(event, action_type="change_medication"),
        )

        self.assertEqual(decision.status, DecisionStatus.DENY)
        self.assertEqual(decision.reason_codes, ("unsupported_action_type",))

    def test_configured_action_holds_when_confidence_is_below_threshold(self):
        event = make_event()
        policy = RuleBasedActionPolicy(
            {"notify_operator": ActionRule(0.7, 0.6, allow_when_review_required=False)}
        )
        decision = GuardianAgent(policy)(
            event,
            make_assessment(event, confidence=0.5),
            make_proposal(event, action_type="notify_operator", confidence=0.9),
        )

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.reason_codes, ("confidence_below_policy_threshold",))

    def test_review_required_holds_actions_not_configured_for_review(self):
        event = make_event()
        policy = RuleBasedActionPolicy(
            {"notify_operator": ActionRule(0.0, 0.0, allow_when_review_required=False)}
        )
        decision = GuardianAgent(policy)(
            event,
            make_assessment(event, is_anomaly=False, requires_review=True, confidence=0.0),
            make_proposal(event, action_type="notify_operator", confidence=0.0),
        )

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.reason_codes, ("review_required",))

    def test_normal_assessment_cannot_authorize_action(self):
        event = make_event()
        decision = GuardianAgent()(
            event,
            make_assessment(event, is_anomaly=False),
            make_proposal(event),
        )

        self.assertEqual(decision.status, DecisionStatus.DENY)
        self.assertEqual(decision.reason_codes, ("assessment_not_actionable",))

    def test_policy_decision_must_match_proposal(self):
        event = make_event()
        proposal = make_proposal(event)
        policy = RecordingPolicy(
            GuardianDecision(
                event_id=event.event_id,
                proposal_id="different-proposal",
                status=DecisionStatus.APPROVE,
                reason_codes=("test",),
                rationale="Mismatched test decision",
            )
        )

        decision = GuardianAgent(policy)(event, make_assessment(event), proposal)

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.proposal_id, proposal.proposal_id)
        self.assertEqual(decision.reason_codes, ("policy_result_mismatch",))

    def test_invalid_action_rule_threshold_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            ActionRule(minimum_proposal_confidence=1.1)


class RecordingPolicy:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result

    def evaluate(self, event, assessment, proposal):
        self.calls += 1
        return self.result or GuardianDecision(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DecisionStatus.APPROVE,
            reason_codes=("recording_policy",),
            rationale="Recorded call.",
        )


if __name__ == "__main__":
    unittest.main()