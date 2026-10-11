import unittest
from datetime import datetime, timezone

from aura.contracts import (
    ActionProposal,
    Assessment,
    DecisionStatus,
    DispatchReceipt,
    DispatchStatus,
    GuardianDecision,
    InputEvent,
    Transcript,
)


class ContractTests(unittest.TestCase):
    def test_input_event_accepts_timezone_and_json_observations(self):
        event = InputEvent(
            event_id="evt-1",
            occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            subject_id="synthetic-1",
            source="scenario-fixture",
            observations={"sensor": {"temperature_c": -2.0}},
            transcript=Transcript(
                text="Aj, min höft", source="kb-whisper", language="sv"
            ),
        )

        self.assertEqual(event.schema_version, "1.0")
        self.assertEqual(event.transcript.source, "kb-whisper")

    def test_input_event_rejects_timezone_naive_timestamp(self):
        with self.assertRaisesRegex(ValueError, "include a timezone"):
            InputEvent(
                event_id="evt-1",
                occurred_at=datetime(2026, 10, 11),
                subject_id="synthetic-1",
                source="scenario-fixture",
            )

    def test_input_event_rejects_non_json_observation(self):
        with self.assertRaisesRegex(ValueError, "not JSON-compatible"):
            InputEvent(
                event_id="evt-1",
                occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
                subject_id="synthetic-1",
                source="scenario-fixture",
                observations={"unsupported": object()},
            )

    def test_assessment_rejects_out_of_range_confidence(self):
        with self.assertRaisesRegex(ValueError, "between 0.0 and 1.0"):
            Assessment(
                event_id="evt-1",
                is_anomaly=True,
                summary="Unusual event",
                confidence=1.1,
            )

    def test_proposal_and_decision_are_bound_to_same_event_and_proposal(self):
        proposal = ActionProposal(
            proposal_id="proposal-1",
            event_id="evt-1",
            action_type="request_human_review",
            rationale="Synthetic demo scenario",
            confidence=0.8,
        )
        decision = GuardianDecision(
            event_id=proposal.event_id,
            proposal_id=proposal.proposal_id,
            status=DecisionStatus.HOLD,
            reason_codes=("human_review_required",),
            rationale="A person should review this proposal.",
        )

        self.assertEqual(decision.event_id, proposal.event_id)
        self.assertEqual(decision.proposal_id, proposal.proposal_id)
        self.assertEqual(
            DispatchReceipt(
                event_id="evt-1",
                proposal_id="proposal-1",
                status=DispatchStatus.SKIPPED,
                message="Approval required.",
            ).status,
            DispatchStatus.SKIPPED,
        )


if __name__ == "__main__":
    unittest.main()