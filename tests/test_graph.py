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
)
from aura.graph import WorkflowOutcome, build_agent_graph, initial_workflow_state


def make_event():
    return InputEvent(
        event_id="evt-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="test",
    )


def make_assessment(event, is_anomaly=True):
    return Assessment(
        event_id=event.event_id,
        is_anomaly=is_anomaly,
        summary="Synthetic test assessment",
        confidence=0.9,
    )


def make_proposal(event):
    return ActionProposal(
        proposal_id="proposal-1",
        event_id=event.event_id,
        action_type="request_human_review",
        rationale="Synthetic test proposal",
        confidence=0.8,
    )


def make_decision(event, proposal, status=DecisionStatus.APPROVE):
    return GuardianDecision(
        event_id=event.event_id,
        proposal_id=proposal.proposal_id,
        status=status,
        reason_codes=("test_policy",),
        rationale="Synthetic test decision",
    )


class AgentGraphTests(unittest.TestCase):
    def test_normal_assessment_stops_before_triage(self):
        event = make_event()
        calls = []

        graph = build_agent_graph(
            supervisor=lambda value: make_assessment(value, is_anomaly=False),
            triage=lambda value, assessment: calls.append("triage"),
            guardian=lambda value, assessment, proposal: calls.append("guardian"),
            dispatcher=lambda value, proposal, decision: calls.append("dispatcher"),
        )

        result = graph.invoke(initial_workflow_state(event))

        self.assertEqual(result["outcome"], WorkflowOutcome.NO_ACTION)
        self.assertEqual(calls, [])

    def test_approved_proposal_reaches_dispatcher(self):
        event = make_event()
        calls = []

        def dispatch(value, proposal, decision):
            calls.append("dispatcher")
            return DispatchReceipt(
                event_id=value.event_id,
                proposal_id=proposal.proposal_id,
                status=DispatchStatus.SUCCEEDED,
                message="Mock ticket created.",
            )

        graph = build_agent_graph(
            supervisor=lambda value: make_assessment(value),
            triage=lambda value, assessment: make_proposal(value),
            guardian=lambda value, assessment, proposal: make_decision(value, proposal),
            dispatcher=dispatch,
        )

        result = graph.invoke(initial_workflow_state(event))

        self.assertEqual(result["outcome"], WorkflowOutcome.DISPATCHED)
        self.assertEqual(calls, ["dispatcher"])

    def test_skipped_dispatch_receipt_is_blocked_not_reported_as_failure(self):
        event = make_event()

        graph = build_agent_graph(
            supervisor=lambda value: make_assessment(value),
            triage=lambda value, assessment: make_proposal(value),
            guardian=lambda value, assessment, proposal: make_decision(value, proposal),
            dispatcher=lambda value, proposal, decision: DispatchReceipt(
                event_id=value.event_id,
                proposal_id=proposal.proposal_id,
                status=DispatchStatus.SKIPPED,
                message="Tool skipped the action.",
            ),
        )

        result = graph.invoke(initial_workflow_state(event))

        self.assertEqual(result["outcome"], WorkflowOutcome.BLOCKED)

    def test_hold_and_deny_never_reach_dispatcher(self):
        for status, expected_outcome in (
            (DecisionStatus.HOLD, WorkflowOutcome.HELD),
            (DecisionStatus.DENY, WorkflowOutcome.DENIED),
        ):
            with self.subTest(status=status):
                event = make_event()
                calls = []
                graph = build_agent_graph(
                    supervisor=lambda value: make_assessment(value),
                    triage=lambda value, assessment: make_proposal(value),
                    guardian=lambda value, assessment, proposal: make_decision(
                        value, proposal, status=status
                    ),
                    dispatcher=lambda value, proposal, decision: calls.append("dispatcher"),
                )

                result = graph.invoke(initial_workflow_state(event))

                self.assertEqual(result["outcome"], expected_outcome)
                self.assertEqual(calls, [])

    def test_mismatched_guardian_approval_blocks_dispatch(self):
        event = make_event()
        calls = []

        def mismatched_decision(value, assessment, proposal):
            decision = make_decision(value, proposal)
            return GuardianDecision(
                event_id=decision.event_id,
                proposal_id="another-proposal",
                status=decision.status,
                reason_codes=decision.reason_codes,
                rationale=decision.rationale,
            )

        graph = build_agent_graph(
            supervisor=lambda value: make_assessment(value),
            triage=lambda value, assessment: make_proposal(value),
            guardian=mismatched_decision,
            dispatcher=lambda value, proposal, decision: calls.append("dispatcher"),
        )

        result = graph.invoke(initial_workflow_state(event))

        self.assertEqual(result["outcome"], WorkflowOutcome.BLOCKED)
        self.assertEqual(calls, [])

    def test_assessment_requiring_review_routes_to_triage(self):
        event = make_event()
        calls = []

        def supervisor(value):
            assessment = make_assessment(value, is_anomaly=False)
            return Assessment(
                event_id=assessment.event_id,
                is_anomaly=False,
                summary="Baseline unavailable",
                confidence=0.0,
                requires_review=True,
            )

        def triage(value, assessment):
            calls.append("triage")
            return make_proposal(value)

        graph = build_agent_graph(
            supervisor=supervisor,
            triage=triage,
            guardian=lambda value, assessment, proposal: make_decision(
                value, proposal, status=DecisionStatus.HOLD
            ),
            dispatcher=lambda value, proposal, decision: calls.append("dispatcher"),
        )

        result = graph.invoke(initial_workflow_state(event))

        self.assertEqual(result["outcome"], WorkflowOutcome.HELD)
        self.assertEqual(calls, ["triage"])


if __name__ == "__main__":
    unittest.main()