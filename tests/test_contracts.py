import unittest
from datetime import datetime, timezone

from aura.contracts import (
    ACTION_CATALOGUE,
    ActionType,
    ActionProposal,
    Assessment,
    DecisionStatus,
    DispatchReceipt,
    DispatchStatus,
    GuardianDecision,
    InputEvent,
    TriageStage,
    Transcript,
)


class ContractTests(unittest.TestCase):
    def test_action_catalogue_is_string_compatible_and_includes_supported_actions(self):
        self.assertEqual(ACTION_CATALOGUE, tuple(action.value for action in ActionType))
        self.assertIn("dismiss", ACTION_CATALOGUE)
        self.assertIn("ask_resident", ACTION_CATALOGUE)
        self.assertIn("remote_followup", ACTION_CATALOGUE)
        self.assertIn("home_visit", ACTION_CATALOGUE)
        self.assertIn("urgent_visit", ACTION_CATALOGUE)
        self.assertIn("notify_relative", ACTION_CATALOGUE)
        self.assertIn("attach_to_planned_visit", ACTION_CATALOGUE)
        self.assertIn("maintenance_ticket", ACTION_CATALOGUE)
        self.assertEqual(ActionType.REQUEST_HUMAN_REVIEW, "request_human_review")

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

    def test_transcript_requires_non_whitespace_text(self):
        for text in ("", "   ", "\n\t"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "transcript text"):
                Transcript(text=text, source="test-stt")

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
        self.assertEqual(proposal.triage_stage, TriageStage.INITIAL)
        post_reply_proposal = ActionProposal(
            proposal_id="proposal-2",
            event_id="evt-1",
            action_type="request_human_review",
            rationale="Resident reply reviewed.",
            confidence=0.8,
            triage_stage=TriageStage.POST_REPLY,
        )
        self.assertEqual(post_reply_proposal.triage_stage.value, "post_reply")
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