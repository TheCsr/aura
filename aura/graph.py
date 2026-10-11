"""LangGraph orchestration for the four replaceable agent components."""

from enum import Enum
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .contracts import (
    ActionProposal,
    Assessment,
    DecisionStatus,
    DispatchReceipt,
    DispatchStatus,
    GuardianDecision,
    InputEvent,
)


class WorkflowOutcome(str, Enum):
    RUNNING = "running"
    NO_ACTION = "no_action"
    PROPOSED = "proposed"
    APPROVED = "approved"
    HELD = "held"
    DENIED = "denied"
    BLOCKED = "blocked"
    DISPATCHED = "dispatched"
    DISPATCH_FAILED = "dispatch_failed"


class WorkflowState(TypedDict):
    event: InputEvent
    assessment: Optional[Assessment]
    proposal: Optional[ActionProposal]
    decision: Optional[GuardianDecision]
    receipt: Optional[DispatchReceipt]
    outcome: WorkflowOutcome


Supervisor = Callable[[InputEvent], Assessment]
Triage = Callable[[InputEvent, Assessment], ActionProposal]
Guardian = Callable[[InputEvent, Assessment, ActionProposal], GuardianDecision]
Dispatcher = Callable[[InputEvent, ActionProposal, GuardianDecision], DispatchReceipt]


def initial_workflow_state(event: InputEvent) -> WorkflowState:
    return {
        "event": event,
        "assessment": None,
        "proposal": None,
        "decision": None,
        "receipt": None,
        "outcome": WorkflowOutcome.RUNNING,
    }


def build_agent_graph(
    *,
    supervisor: Supervisor,
    triage: Triage,
    guardian: Guardian,
    dispatcher: Dispatcher,
) -> Any:
    """Build a graph from independently replaceable agent implementations."""

    def run_supervisor(state: WorkflowState) -> dict[str, Any]:
        assessment = supervisor(state["event"])
        if not isinstance(assessment, Assessment) or assessment.event_id != state["event"].event_id:
            return {
                "assessment": assessment if isinstance(assessment, Assessment) else None,
                "outcome": WorkflowOutcome.BLOCKED,
            }
        outcome = (
            WorkflowOutcome.RUNNING
            if assessment.is_anomaly or assessment.requires_review
            else WorkflowOutcome.NO_ACTION
        )
        return {"assessment": assessment, "outcome": outcome}

    def run_triage(state: WorkflowState) -> dict[str, Any]:
        event = state["event"]
        assessment = state["assessment"]
        if assessment is None or assessment.event_id != event.event_id:
            return {"outcome": WorkflowOutcome.BLOCKED}
        proposal = triage(event, assessment)
        if not isinstance(proposal, ActionProposal) or proposal.event_id != event.event_id:
            return {
                "proposal": proposal if isinstance(proposal, ActionProposal) else None,
                "outcome": WorkflowOutcome.BLOCKED,
            }
        return {"proposal": proposal, "outcome": WorkflowOutcome.PROPOSED}

    def run_guardian(state: WorkflowState) -> dict[str, Any]:
        event = state["event"]
        assessment = state["assessment"]
        proposal = state["proposal"]
        if (
            assessment is None
            or proposal is None
            or assessment.event_id != event.event_id
            or proposal.event_id != event.event_id
        ):
            return {"outcome": WorkflowOutcome.BLOCKED}

        decision = guardian(event, assessment, proposal)
        if not isinstance(decision, GuardianDecision):
            return {"outcome": WorkflowOutcome.BLOCKED}
        if decision.event_id != event.event_id or decision.proposal_id != proposal.proposal_id:
            return {"decision": decision, "outcome": WorkflowOutcome.BLOCKED}

        outcome_by_decision = {
            DecisionStatus.APPROVE: WorkflowOutcome.APPROVED,
            DecisionStatus.HOLD: WorkflowOutcome.HELD,
            DecisionStatus.DENY: WorkflowOutcome.DENIED,
        }
        return {"decision": decision, "outcome": outcome_by_decision[decision.status]}

    def run_dispatcher(state: WorkflowState) -> dict[str, Any]:
        event = state["event"]
        proposal = state["proposal"]
        decision = state["decision"]
        if (
            proposal is None
            or decision is None
            or decision.status is not DecisionStatus.APPROVE
            or proposal.event_id != event.event_id
            or decision.event_id != event.event_id
            or decision.proposal_id != proposal.proposal_id
        ):
            return {"outcome": WorkflowOutcome.BLOCKED}

        receipt = dispatcher(event, proposal, decision)
        if (
            not isinstance(receipt, DispatchReceipt)
            or receipt.event_id != event.event_id
            or receipt.proposal_id != proposal.proposal_id
        ):
            return {
                "receipt": receipt if isinstance(receipt, DispatchReceipt) else None,
                "outcome": WorkflowOutcome.BLOCKED,
            }
        if receipt.status is DispatchStatus.SUCCEEDED:
            outcome = WorkflowOutcome.DISPATCHED
        elif receipt.status is DispatchStatus.FAILED:
            outcome = WorkflowOutcome.DISPATCH_FAILED
        else:
            outcome = WorkflowOutcome.BLOCKED
        return {"receipt": receipt, "outcome": outcome}

    def route_after_supervisor(state: WorkflowState) -> str:
        assessment = state["assessment"]
        if (
            state["outcome"] is WorkflowOutcome.RUNNING
            and assessment is not None
            and assessment.event_id == state["event"].event_id
            and (assessment.is_anomaly or assessment.requires_review)
        ):
            return "triage"
        return "end"

    def route_after_triage(state: WorkflowState) -> str:
        proposal = state["proposal"]
        if (
            state["outcome"] is WorkflowOutcome.PROPOSED
            and proposal is not None
            and proposal.event_id == state["event"].event_id
        ):
            return "guardian"
        return "end"

    def route_after_guardian(state: WorkflowState) -> str:
        decision = state["decision"]
        proposal = state["proposal"]
        if (
            state["outcome"] is WorkflowOutcome.APPROVED
            and decision is not None
            and proposal is not None
            and decision.event_id == state["event"].event_id
            and decision.proposal_id == proposal.proposal_id
        ):
            return "dispatcher"
        return "end"

    graph = StateGraph(WorkflowState)
    graph.add_node("supervisor", run_supervisor)
    graph.add_node("triage", run_triage)
    graph.add_node("guardian", run_guardian)
    graph.add_node("dispatcher", run_dispatcher)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor, {"triage": "triage", "end": END}
    )
    graph.add_conditional_edges("triage", route_after_triage, {"guardian": "guardian", "end": END})
    graph.add_conditional_edges(
        "guardian", route_after_guardian, {"dispatcher": "dispatcher", "end": END}
    )
    graph.add_edge("dispatcher", END)
    return graph.compile()