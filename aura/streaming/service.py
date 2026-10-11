"""Application workflow for a streamed sensor event and its correlated reply."""

from dataclasses import dataclass, replace
from threading import RLock
from typing import Dict, Optional

from ..agents.dispatcher import DispatcherAgent
from ..agents.guardian import GuardianAgent
from ..agents.supervisor import SupervisorAgent
from ..agents.triage import TriageAgent
from ..contracts import (
    ActionProposal,
    Assessment,
    DispatchReceipt,
    GuardianDecision,
    InputEvent,
)
from ..graph import WorkflowOutcome
from ..sensors.validation import SensorEventValidator
from ..voice.contracts import CorrelatedVoiceReply, VoicePrompt
from .voice import VoiceResponder


@dataclass(frozen=True)
class StreamingResult:
    event_id: str
    outcome: WorkflowOutcome
    assessment: Assessment
    voice_prompt: Optional[VoicePrompt] = None
    voice_reply: Optional[CorrelatedVoiceReply] = None
    proposal: Optional[ActionProposal] = None
    decision: Optional[GuardianDecision] = None
    receipt: Optional[DispatchReceipt] = None


class StreamingCareService:
    """Handle a sensor event from validation through an approved dashboard action."""

    def __init__(
        self,
        supervisor: SupervisorAgent,
        voice_responder: VoiceResponder,
        triage: TriageAgent,
        guardian: GuardianAgent,
        dispatcher: DispatcherAgent,
        sensor_validator: Optional[SensorEventValidator] = None,
    ) -> None:
        self._supervisor = supervisor
        self._voice_responder = voice_responder
        self._triage = triage
        self._guardian = guardian
        self._dispatcher = dispatcher
        self._sensor_validator = sensor_validator or SensorEventValidator()
        self._processed: Dict[str, StreamingResult] = {}
        self._lock = RLock()

    def handle(self, event: InputEvent) -> StreamingResult:
        with self._lock:
            previous = self._processed.get(event.event_id)
            if previous is not None:
                return previous
            result = self._process(event)
            self._processed[event.event_id] = result
            return result

    def __call__(self, event: InputEvent) -> StreamingResult:
        return self.handle(event)

    def _process(self, event: InputEvent) -> StreamingResult:
        self._sensor_validator.validate(event)
        assessment = self._supervisor(event)
        if not assessment.is_anomaly and not assessment.requires_review:
            return StreamingResult(
                event_id=event.event_id,
                outcome=WorkflowOutcome.NO_ACTION,
                assessment=assessment,
            )

        voice_prompt, voice_reply = self._voice_responder.ask_and_capture(event, assessment)
        if (
            voice_prompt.event_id != event.event_id
            or voice_reply.event_id != event.event_id
            or voice_prompt.conversation_id != voice_reply.conversation_id
        ):
            raise ValueError("voice response does not correlate to the sensor event and prompt")

        enriched_event = replace(
            event,
            transcript=voice_reply.transcript,
            observations={
                **event.observations,
                "vocal_signal_features": dict(voice_reply.signal_analysis.features),
            },
        )
        proposal = self._triage(enriched_event, assessment)
        decision = self._guardian(enriched_event, assessment, proposal)

        if decision.status.value == "approve":
            receipt = self._dispatcher(enriched_event, proposal, decision)
            outcome = (
                WorkflowOutcome.DISPATCHED
                if receipt.status.value == "succeeded"
                else WorkflowOutcome.DISPATCH_FAILED
            )
        else:
            receipt = None
            outcome = (
                WorkflowOutcome.HELD
                if decision.status.value == "hold"
                else WorkflowOutcome.DENIED
            )
        return StreamingResult(
            event_id=event.event_id,
            outcome=outcome,
            assessment=assessment,
            voice_prompt=voice_prompt,
            voice_reply=voice_reply,
            proposal=proposal,
            decision=decision,
            receipt=receipt,
        )