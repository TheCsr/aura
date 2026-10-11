import unittest
from datetime import datetime, timezone

from aura.agents.dispatcher import (
    ActionToolRegistry,
    DispatcherAgent,
    MockTicketTool,
)
from aura.contracts import (
    ActionProposal,
    DecisionStatus,
    DispatchReceipt,
    DispatchStatus,
    GuardianDecision,
    InputEvent,
)


def make_event(event_id="evt-1"):
    return InputEvent(
        event_id=event_id,
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="test",
    )


def make_proposal(event, proposal_id="proposal-1", action_type="request_human_review"):
    return ActionProposal(
        proposal_id=proposal_id,
        event_id=event.event_id,
        action_type=action_type,
        rationale="Synthetic test proposal",
        confidence=0.8,
        parameters={"priority": "review"},
    )


def make_decision(event, proposal, status=DecisionStatus.APPROVE, proposal_id=None):
    return GuardianDecision(
        event_id=event.event_id,
        proposal_id=proposal.proposal_id if proposal_id is None else proposal_id,
        status=status,
        reason_codes=("test_policy",),
        rationale="Synthetic test decision",
    )


class RecordingTool:
    def __init__(self, receipt=None, error=None):
        self.calls = []
        self.receipt = receipt
        self.error = error

    def execute(self, event, proposal):
        self.calls.append((event, proposal))
        if self.error is not None:
            raise self.error
        return self.receipt


class DispatcherAgentTests(unittest.TestCase):
    def test_missing_or_mismatched_approval_skips_tool(self):
        event = make_event()
        proposal = make_proposal(event)
        tool = RecordingTool()
        dispatcher = DispatcherAgent(ActionToolRegistry({proposal.action_type: tool}))

        no_approval = dispatcher(event, proposal, None)
        wrong_proposal = dispatcher(
            event,
            proposal,
            make_decision(event, proposal, proposal_id="other-proposal"),
        )
        denied = dispatcher(
            event,
            proposal,
            make_decision(event, proposal, status=DecisionStatus.DENY),
        )

        self.assertEqual(no_approval.status, DispatchStatus.SKIPPED)
        self.assertEqual(wrong_proposal.status, DispatchStatus.SKIPPED)
        self.assertEqual(denied.status, DispatchStatus.SKIPPED)
        self.assertEqual(tool.calls, [])

    def test_approved_proposal_creates_idempotent_mock_ticket(self):
        event = make_event()
        proposal = make_proposal(event)
        decision = make_decision(event, proposal)
        mock_tool = MockTicketTool(ticket_id_factory=lambda: "ticket-1")
        dispatcher = DispatcherAgent(
            ActionToolRegistry({proposal.action_type: mock_tool})
        )

        first = dispatcher(event, proposal, decision)
        retry = dispatcher(event, proposal, decision)

        self.assertEqual(first.status, DispatchStatus.SUCCEEDED)
        self.assertEqual(first.external_reference, "ticket-1")
        self.assertEqual(retry.external_reference, first.external_reference)
        self.assertEqual(len(mock_tool.tickets), 1)
        self.assertEqual(mock_tool.tickets[0].event_id, event.event_id)

    def test_unregistered_action_returns_failure_without_tool_call(self):
        event = make_event()
        proposal = make_proposal(event, action_type="unknown_action")
        dispatcher = DispatcherAgent(ActionToolRegistry({}))

        receipt = dispatcher(event, proposal, make_decision(event, proposal))

        self.assertEqual(receipt.status, DispatchStatus.FAILED)
        self.assertIn("No tool is registered", receipt.message)

    def test_tool_exception_returns_failed_receipt(self):
        event = make_event()
        proposal = make_proposal(event)
        tool = RecordingTool(error=RuntimeError("private adapter detail"))
        dispatcher = DispatcherAgent(ActionToolRegistry({proposal.action_type: tool}))

        receipt = dispatcher(event, proposal, make_decision(event, proposal))

        self.assertEqual(receipt.status, DispatchStatus.FAILED)
        self.assertEqual(receipt.message, "Action tool execution failed.")
        self.assertEqual(len(tool.calls), 1)

    def test_receipt_with_wrong_identity_is_rejected(self):
        event = make_event()
        proposal = make_proposal(event)
        tool = RecordingTool(receipt=object())
        dispatcher = DispatcherAgent(ActionToolRegistry({proposal.action_type: tool}))

        receipt = dispatcher(event, proposal, make_decision(event, proposal))

        self.assertEqual(receipt.status, DispatchStatus.FAILED)
        self.assertIn("invalid receipt", receipt.message)

    def test_tool_receipt_for_another_proposal_is_rejected(self):
        event = make_event()
        proposal = make_proposal(event)
        tool = RecordingTool(
            receipt=DispatchReceipt(
                event_id=event.event_id,
                proposal_id="other-proposal",
                status=DispatchStatus.SUCCEEDED,
                message="Incorrect receipt",
            )
        )
        dispatcher = DispatcherAgent(ActionToolRegistry({proposal.action_type: tool}))

        receipt = dispatcher(event, proposal, make_decision(event, proposal))

        self.assertEqual(receipt.status, DispatchStatus.FAILED)
        self.assertIn("did not match", receipt.message)

    def test_approval_for_another_event_skips_tool(self):
        event = make_event()
        other_event = make_event("evt-2")
        proposal = make_proposal(event)
        tool = RecordingTool()
        dispatcher = DispatcherAgent(ActionToolRegistry({proposal.action_type: tool}))

        receipt = dispatcher(
            event,
            proposal,
            make_decision(other_event, make_proposal(other_event)),
        )

        self.assertEqual(receipt.status, DispatchStatus.SKIPPED)
        self.assertEqual(tool.calls, [])


if __name__ == "__main__":
    unittest.main()