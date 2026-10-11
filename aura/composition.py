"""Default mock-backed composition of the four AURA agents."""

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .agents.dispatcher import ActionToolRegistry, DispatcherAgent, MockTicketTool
from .agents.guardian import GuardianAgent
from .agents.supervisor import AssessmentStrategy, SupervisorAgent
from .agents.supervisor_llm import LlmAssessmentStrategy
from .agents.triage import TriageAgent
from .contracts import InputEvent, JsonValue
from .graph import WorkflowState, build_agent_graph, initial_workflow_state
from .ports import ActionPolicy, ActionTool, BaselineStore, ChatModel
from .testing import (
    DeterministicFakeAssessmentChatModel,
    DeterministicFakeChatModel,
    InMemoryBaselineStore,
)


@dataclass
class DemoSystem:
    """Runnable composition with inspectable mock tickets and baseline store."""

    graph: Any
    baseline_store: BaselineStore
    mock_ticket_tool: MockTicketTool
    dispatcher_tool: ActionTool

    def run(self, event: InputEvent) -> WorkflowState:
        return self.graph.invoke(initial_workflow_state(event))


def create_demo_system(
    baselines: Optional[Mapping[str, Mapping[str, JsonValue]]] = None,
    *,
    baseline_store: Optional[BaselineStore] = None,
    chat_model: Optional[ChatModel] = None,
    assessment_model: Optional[ChatModel] = None,
    supervisor_strategy: Optional[AssessmentStrategy] = None,
    guardian_policy: Optional[ActionPolicy] = None,
    dispatcher_tool: Optional[ActionTool] = None,
    allowed_action_types: Sequence[str] = ("request_human_review",),
    numeric_tolerances: Optional[Mapping[str, float]] = None,
    max_triage_context_chars: int = 12000,
    proposal_id_factory: Optional[Callable[[], str]] = None,
    ticket_id_factory: Optional[Callable[[], str]] = None,
) -> DemoSystem:
    """Create a complete offline-ready graph with replaceable components.

    By default, model calls are deterministic, baselines are in memory, and all
    dispatches go to an in-memory mock ticket tool. No external services are used.
    """

    if baseline_store is not None and baselines is not None:
        raise ValueError("provide either baselines or baseline_store, not both")

    selected_baseline_store = (
        baseline_store
        if baseline_store is not None
        else InMemoryBaselineStore(baselines)
    )
    selected_chat_model = chat_model if chat_model is not None else DeterministicFakeChatModel()
    selected_assessment_strategy = (
        supervisor_strategy
        if supervisor_strategy is not None
        else LlmAssessmentStrategy(
            assessment_model
            if assessment_model is not None
            else DeterministicFakeAssessmentChatModel(numeric_tolerances=numeric_tolerances),
            numeric_tolerances=numeric_tolerances,
        )
    )
    ticket_tool = MockTicketTool(ticket_id_factory=ticket_id_factory)
    selected_dispatcher_tool = (
        dispatcher_tool if dispatcher_tool is not None else ticket_tool
    )
    action_types = tuple(allowed_action_types)

    supervisor = SupervisorAgent(
        selected_baseline_store,
        strategy=selected_assessment_strategy,
    )
    triage = TriageAgent(
        selected_chat_model,
        allowed_action_types=action_types,
        max_context_chars=max_triage_context_chars,
        proposal_id_factory=proposal_id_factory,
    )
    guardian = GuardianAgent(policy=guardian_policy)
    dispatcher = DispatcherAgent(
        ActionToolRegistry(
            {action_type: selected_dispatcher_tool for action_type in action_types}
        )
    )
    graph = build_agent_graph(
        supervisor=supervisor,
        triage=triage,
        guardian=guardian,
        dispatcher=dispatcher,
    )
    return DemoSystem(
        graph=graph,
        baseline_store=selected_baseline_store,
        mock_ticket_tool=ticket_tool,
        dispatcher_tool=selected_dispatcher_tool,
    )