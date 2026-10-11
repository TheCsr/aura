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
    subject_id: str
    created_at: datetime
    status: str
    summary: str
    observations: Mapping[str, JsonValue]


class MockDashboardAdapter:
    """Record approved dispatcher actions in memory; performs no network I/O."""

    SUPPORTED_ACTION = "request_human_review"

    def __init__(self, record_id_factory: Optional[Callable[[], str]] = None) -> None:
        self._record_id_factory = record_id_factory or (lambda: f"dashboard-{uuid4().hex}")
        self._lock = Lock()
        self._records: Dict[Tuple[str, str], DashboardRecord] = {}

    @property
    def records(self) -> Tuple[DashboardRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def execute(self, event: InputEvent, proposal: ActionProposal) -> DispatchReceipt:
        if proposal.action_type != self.SUPPORTED_ACTION:
            return self._failed(event, proposal, "Dashboard adapter only accepts human-review requests.")

        key = (event.event_id, proposal.proposal_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                record_id = self._record_id_factory()
                if not isinstance(record_id, str) or not record_id.strip():
                    return self._failed(event, proposal, "Dashboard record ID generation failed.")
                record = DashboardRecord(
                    record_id=record_id,
                    event_id=event.event_id,
                    proposal_id=proposal.proposal_id,
                    subject_id=event.subject_id,
                    created_at=datetime.now(timezone.utc),
                    status="awaiting_human_review",
                    summary=proposal.rationale,
                    observations=deepcopy(dict(event.observations)),
                )
                self._records[key] = record

        return DispatchReceipt(
            event_id=event.event_id,
            proposal_id=proposal.proposal_id,
            status=DispatchStatus.SUCCEEDED,
            message="Mock dashboard record created.",
            external_reference=record.record_id,
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