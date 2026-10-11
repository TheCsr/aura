"""Versioned, provider-neutral data contracts shared by agent components."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Dict, List, Mapping, Optional, Tuple, Union


JsonValue = Union[
    str,
    int,
    float,
    bool,
    None,
    List["JsonValue"],
    Dict[str, "JsonValue"],
]


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_json_value(value: JsonValue, path: str) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError(f"{path} must not contain NaN or infinity")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a value that is not JSON-compatible")


def _validate_json_object(name: str, value: Mapping[str, JsonValue]) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} keys must be non-empty strings")
        _validate_json_value(item, f"{name}.{key}")


def _validate_confidence(value: float) -> None:
    if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True)
class Transcript:
    """Speech-to-text result, kept separate from agent reasoning."""

    text: str
    source: str
    language: Optional[str] = None
    model: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("transcript text must be a string")
        _require_text("transcript source", self.source)


@dataclass(frozen=True)
class InputEvent:
    """Generic versioned input event; domain-specific data lives in observations."""

    event_id: str
    occurred_at: datetime
    subject_id: str
    source: str
    observations: Mapping[str, JsonValue] = field(default_factory=dict)
    transcript: Optional[Transcript] = None
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("subject_id", self.subject_id)
        _require_text("source", self.source)
        _require_text("schema_version", self.schema_version)
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("occurred_at must be a datetime")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        _validate_json_object("observations", self.observations)


@dataclass(frozen=True)
class EvidenceItem:
    """A traceable piece of evidence used by an agent assessment."""

    source: str
    description: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("evidence source", self.source)
        _require_text("evidence description", self.description)
        _validate_json_object("evidence details", self.details)


@dataclass(frozen=True)
class Assessment:
    """Supervisor result describing a signal and the evidence behind it."""

    event_id: str
    is_anomaly: bool
    summary: str
    confidence: float
    evidence: Tuple[EvidenceItem, ...] = ()
    requires_review: bool = False

    def __post_init__(self) -> None:
        _require_text("assessment event_id", self.event_id)
        _require_text("assessment summary", self.summary)
        _validate_confidence(self.confidence)
        if not isinstance(self.is_anomaly, bool):
            raise ValueError("is_anomaly must be a boolean")
        if not isinstance(self.requires_review, bool):
            raise ValueError("requires_review must be a boolean")


@dataclass(frozen=True)
class ActionProposal:
    """Triage proposal; this type does not imply authorization to act."""

    proposal_id: str
    event_id: str
    action_type: str
    rationale: str
    confidence: float
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("proposal_id", self.proposal_id)
        _require_text("proposal event_id", self.event_id)
        _require_text("action_type", self.action_type)
        _require_text("proposal rationale", self.rationale)
        _validate_confidence(self.confidence)
        _validate_json_object("proposal parameters", self.parameters)


class DecisionStatus(str, Enum):
    APPROVE = "approve"
    HOLD = "hold"
    DENY = "deny"


@dataclass(frozen=True)
class GuardianDecision:
    """Guardian's explicit policy outcome for one event-bound proposal."""

    event_id: str
    proposal_id: str
    status: DecisionStatus
    reason_codes: Tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        _require_text("decision event_id", self.event_id)
        _require_text("decision proposal_id", self.proposal_id)
        _require_text("decision rationale", self.rationale)
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("status must be a DecisionStatus")
        if any(not isinstance(code, str) or not code.strip() for code in self.reason_codes):
            raise ValueError("reason_codes must contain non-empty strings")


class DispatchStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DispatchReceipt:
    """Result returned by a dispatcher tool, including mock implementations."""

    event_id: str
    proposal_id: str
    status: DispatchStatus
    message: str
    external_reference: Optional[str] = None

    def __post_init__(self) -> None:
        _require_text("receipt event_id", self.event_id)
        _require_text("receipt proposal_id", self.proposal_id)
        _require_text("receipt message", self.message)
        if not isinstance(self.status, DispatchStatus):
            raise ValueError("status must be a DispatchStatus")