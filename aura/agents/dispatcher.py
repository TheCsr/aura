"""Approval-gated Dispatcher and replaceable action-tool components."""

from dataclasses import dataclass
from threading import Lock
from typing import Callable, Mapping, Optional, Tuple
from uuid import uuid4

from ..contracts import (
    ActionProposal,
    DecisionStatus,
    DispatchReceipt,
    DispatchStatus,
    GuardianDecision,
    InputEvent,
    JsonValue,
)
from ..ports import ActionTool


class GuardianApprovalVerifier:
    """Verify approval is explicit and bound to this event and proposal."""

    def is_approved(
        self,
        event: InputEvent,
        proposal: ActionProposal,
        decision: GuardianDecision,
    ) -> bool:
        return (
            isinstance(decision, GuardianDecision)
            and decision.status is DecisionStatus.APPROVE
            and decision.event_id == event.event_id == proposal.event_id
            and decision.proposal_id == proposal.proposal_id
        )


class ActionToolRegistry:
    """Resolve proposal action types to explicitly registered tools."""

    def __init__(self, tools: Mapping[str, ActionTool]) -> None:
        self._tools = dict(tools)
        for action_type, tool in self._tools.items():
            if not isinstance(action_type, str) or not action_type.strip():
                raise ValueError("registered action types must be non-empty strings")
            if not callable(getattr(tool, "execute", None)):
                raise ValueError(f"tool for {action_type!r} must define execute()")

    def resolve(self, action_type: str) -> Optional[ActionTool]:
        return self._tools.get(action_type)


class DispatchReceiptValidator:
    """Ensure a tool result belongs to the event and proposal being dispatched."""

    def validate(
        self,
        event: InputEvent,
        proposal: ActionProposal,
        receipt: DispatchReceipt,
    ) -> DispatchReceipt:
        if not isinstance(receipt, DispatchReceipt):
            return self._failed(event, proposal, "Tool returned an invalid receipt.")
        if receipt.event_id != event.event_id or receipt.proposal_id != proposal.proposal_id:
            return self._failed(event, proposal, "Tool receipt did not match the dispatched proposal.")
        return receipt

    @staticmethod
    def _failed(event: InputEvent, proposal: ActionProposal, message: str) -> DispatchReceipt:
        return DispatchReceipt(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DispatchStatus.FAILED,
            message=message,
        )


class DispatcherAgent:
    """Dispatch an action only after verifying Guardian's matching approval."""

    def __init__(
        self,
        tool_registry: ActionToolRegistry,
        *,
        approval_verifier: Optional[GuardianApprovalVerifier] = None,
        receipt_validator: Optional[DispatchReceiptValidator] = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._approval_verifier = approval_verifier or GuardianApprovalVerifier()
        self._receipt_validator = receipt_validator or DispatchReceiptValidator()

    def run(
        self,
        event: InputEvent,
        proposal: ActionProposal,
        decision: GuardianDecision,
    ) -> DispatchReceipt:
        if not self._approval_verifier.is_approved(event, proposal, decision):
            return DispatchReceipt(
                event_id=event.event_id,
                proposal_id=proposal.proposal_id,
                status=DispatchStatus.SKIPPED,
                message="Matching Guardian approval is required; no tool was called.",
            )

        tool = self._tool_registry.resolve(proposal.action_type)
        if tool is None:
            return DispatchReceipt(
                event_id=event.event_id,
                proposal_id=proposal.proposal_id,
                status=DispatchStatus.FAILED,
                message="No tool is registered for this action type.",
            )

        try:
            receipt = tool.execute(event, proposal)
        except Exception:
            return DispatchReceipt(
                event_id=event.event_id,
                proposal_id=proposal.proposal_id,
                status=DispatchStatus.FAILED,
                message="Action tool execution failed.",
            )
        return self._receipt_validator.validate(event, proposal, receipt)

    def __call__(
        self,
        event: InputEvent,
        proposal: ActionProposal,
        decision: GuardianDecision,
    ) -> DispatchReceipt:
        return self.run(event, proposal, decision)


@dataclass(frozen=True)
class MockTicket:
    ticket_id: str
    event_id: str
    proposal_id: str
    action_type: str
    parameters: Mapping[str, JsonValue]


class MockTicketTool:
    """Record mock tickets in memory and return the same result for retries."""

    def __init__(self, ticket_id_factory: Optional[Callable[[], str]] = None) -> None:
        self._ticket_id_factory = ticket_id_factory or (lambda: f"mock-{uuid4().hex}")
        self._lock = Lock()
        self._tickets = {}

    @property
    def tickets(self) -> Tuple[MockTicket, ...]:
        with self._lock:
            return tuple(self._tickets.values())

    def execute(self, event: InputEvent, proposal: ActionProposal) -> DispatchReceipt:
        key = (event.event_id, proposal.proposal_id)
        with self._lock:
            existing = self._tickets.get(key)
            if existing is not None:
                return self._receipt(event, proposal, existing)

            ticket_id = self._ticket_id_factory()
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                return DispatchReceipt(
                    event_id=event.event_id,
                    proposal_id=proposal.proposal_id,
                    status=DispatchStatus.FAILED,
                    message="Mock ticket ID generation failed.",
                )

            ticket = MockTicket(
                ticket_id=ticket_id,
                event_id=event.event_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                parameters=dict(proposal.parameters),
            )
            self._tickets[key] = ticket
            return self._receipt(event, proposal, ticket)

    @staticmethod
    def _receipt(
        event: InputEvent,
        proposal: ActionProposal,
        ticket: MockTicket,
    ) -> DispatchReceipt:
        return DispatchReceipt(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DispatchStatus.SUCCEEDED,
            message="Mock ticket recorded.",
            external_reference=ticket.ticket_id,
        )