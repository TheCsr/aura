"""Replaceable interfaces for models, context, policies, and action tools."""

from typing import Mapping, Optional, Protocol

from .contracts import (
    ActionProposal,
    Assessment,
    DispatchReceipt,
    GuardianDecision,
    InputEvent,
    JsonValue,
    Transcript,
)


class ChatModel(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return model output; callers are responsible for schema validation."""


class SpeechToText(Protocol):
    def transcribe(
        self, audio: bytes, *, filename: str, language: Optional[str] = None
    ) -> Transcript:
        """Convert audio into a transcript without coupling it to an agent."""


class BaselineStore(Protocol):
    def get_baseline(self, subject_id: str) -> Optional[Mapping[str, JsonValue]]:
        """Return baseline context for a subject, if one is available."""


class ActionPolicy(Protocol):
    def evaluate(
        self,
        event: InputEvent,
        assessment: Assessment,
        proposal: ActionProposal,
    ) -> GuardianDecision:
        """Return a deterministic approval, hold, or denial decision."""


class ActionTool(Protocol):
    def execute(self, event: InputEvent, proposal: ActionProposal) -> DispatchReceipt:
        """Execute an already-approved proposal through a replaceable tool."""