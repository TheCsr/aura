"""In-memory dashboard adapter for approved human-review requests."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Dict, Mapping, Optional, Tuple
from uuid import uuid4

from ..contracts import (
    ActionProposal,
    DispatchReceipt,
    DispatchStatus,
    InputEvent,
    JsonValue,
)


@dataclass(frozen=True)
class DashboardRecord:
    record_id: str
    event_id: str
    proposal_id: str
    action_type: str
    triage_stage: str
    priority: str
    subject_id: str
    created_at: datetime
    status: str
    summary: str
    observations: Mapping[str, JsonValue]


@dataclass(frozen=True)
class DashboardActionRecord:
    record_id: str
    event_id: str
    proposal_id: str
    action_type: str
    record_type: str
    subject_id: str
    created_at: datetime
    reason: str
    parameters: Mapping[str, JsonValue]


class MockDashboardAdapter:
    """Store approved demo tickets and non-ticket action records in memory."""

    TICKET_ACTIONS = frozenset({
        "request_human_review",
        "maintenance_ticket",
        "remote_followup",
        "home_visit",
        "urgent_visit",
    })
    AUDIT_ACTION_TYPES = {
        "dismiss": "dismissal_audit",
        "notify_relative": "relative_notification",
        "attach_to_planned_visit": "planned_visit_note",
    }
    SUPPORTED_ACTIONS = TICKET_ACTIONS | frozenset(AUDIT_ACTION_TYPES)

    _ACTION_PRIORITIES = {
        "maintenance_ticket": "Normal",
        "remote_followup": "Normal",
        "home_visit": "High",
        "urgent_visit": "Urgent",
    }

    def __init__(self, record_id_factory: Optional[Callable[[], str]] = None) -> None:
        self._record_id_factory = record_id_factory or (lambda: f"dashboard-{uuid4().hex}")
        self._lock = Lock()
        self._records: Dict[Tuple[str, str], DashboardRecord] = {}
        self._action_records: Dict[Tuple[str, str], DashboardActionRecord] = {}

    @property
    def records(self) -> Tuple[DashboardRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    @property
    def action_records(self) -> Tuple[DashboardActionRecord, ...]:
        with self._lock:
            return tuple(self._action_records.values())

    def execute(self, event: InputEvent, proposal: ActionProposal) -> DispatchReceipt:
        if proposal.action_type not in self.SUPPORTED_ACTIONS:
            return self._failed(event, proposal, "Dashboard adapter does not support this action.")

        key = (event.event_id, proposal.proposal_id)
        with self._lock:
            if proposal.action_type in self.TICKET_ACTIONS:
                record = self._records.get(key)
                if record is None:
                    record_id = self._record_id_factory()
                    if not isinstance(record_id, str) or not record_id.strip():
                        return self._failed(event, proposal, "Dashboard ticket ID generation failed.")
                    requested_priority = proposal.parameters.get("priority")
                    if requested_priority not in {"Normal", "High", "Urgent"}:
                        requested_priority = self._ACTION_PRIORITIES.get(
                            proposal.action_type,
                            "High" if "critical" in event.event_id else "Normal",
                        )
                    priority = requested_priority
                    record = DashboardRecord(
                        record_id=record_id,
                        event_id=event.event_id,
                        proposal_id=proposal.proposal_id,
                        action_type=proposal.action_type,
                        triage_stage=proposal.triage_stage.value,
                        priority=priority,
                        subject_id=event.subject_id,
                        created_at=datetime.now(timezone.utc),
                        status=(
                            "maintenance_required"
                            if proposal.action_type == "maintenance_ticket"
                            else "awaiting_human_review"
                        ),
                        summary=proposal.rationale,
                        observations=deepcopy(dict(event.observations)),
                    )
                    self._records[key] = record
                record_id = record.record_id
                message = f"Mock {proposal.action_type} ticket created."
            else:
                record = self._action_records.get(key)
                if record is None:
                    record_id = self._record_id_factory()
                    if not isinstance(record_id, str) or not record_id.strip():
                        return self._failed(event, proposal, "Dashboard action record ID generation failed.")
                    record = DashboardActionRecord(
                        record_id=record_id,
                        event_id=event.event_id,
                        proposal_id=proposal.proposal_id,
                        action_type=proposal.action_type,
                        record_type=self.AUDIT_ACTION_TYPES[proposal.action_type],
                        subject_id=event.subject_id,
                        created_at=datetime.now(timezone.utc),
                        reason=proposal.rationale,
                        parameters=deepcopy(dict(proposal.parameters)),
                    )
                    self._action_records[key] = record
                record_id = record.record_id
                message = f"Mock {record.record_type} recorded."

        return DispatchReceipt(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DispatchStatus.SUCCEEDED,
            message=message,
            external_reference=record_id,
        )

    @staticmethod
    def _failed(
        event: InputEvent,
        proposal: ActionProposal,
        message: str,
    ) -> DispatchReceipt:
        return DispatchReceipt(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DispatchStatus.FAILED,
            message=message,
        )


class MockDashboardActionTool:
    """Bind one in-memory dashboard tool to one proposal action type."""

    def __init__(self, dashboard: MockDashboardAdapter, action_type: str) -> None:
        if action_type not in dashboard.SUPPORTED_ACTIONS:
            raise ValueError("action_type must be supported by the dashboard adapter")
        self._dashboard = dashboard
        self._action_type = action_type

    def execute(self, event: InputEvent, proposal: ActionProposal) -> DispatchReceipt:
        if proposal.action_type != self._action_type:
            return self._dashboard._failed(
                event,
                proposal,
                "Action tool does not match the proposal action type.",
            )
        return self._dashboard.execute(event, proposal)