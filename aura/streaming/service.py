"""Application workflow for a streamed sensor event and its correlated reply."""

from dataclasses import dataclass, replace
from copy import deepcopy
from hashlib import sha256
import math
import os
import re
from threading import RLock
from time import monotonic
from typing import Callable, Dict, Optional, Tuple
from uuid import uuid4

from ..agents.dispatcher import DispatcherAgent
from ..agents.guardian import GuardianAgent
from ..agents.supervisor import SupervisorAgent
from ..agents.triage import TriageAgent
from ..contracts import (
    ActionType,
    ActionProposal,
    Assessment,
    AssessmentSeverity,
    DispatchReceipt,
    DispatchStatus,
    EvidenceItem,
    GuardianDecision,
    InputEvent,
    TriageStage,
)
from ..graph import WorkflowOutcome
from ..sensors.validation import SensorEventValidator
from ..sensors.quality import SensorQualityFinding, SensorQualityMonitor
from ..voice.contracts import AudioRecording, CorrelatedVoiceReply, SpeakerReceipt, VoicePrompt
from ..voice.ports import VoiceTranscriber, VocalSignalAnalyzer
from .voice import SensorAnomalyPromptBuilder, VoiceResponder


_RESIDENT_DISTRESS_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bmår\s+inte\s+(?:så\s+)?bra\b",
    r"\bmår\s+dåligt\b",
    r"\binte\s+(?:så\s+)?(?:bra|okej)\s+hemma\b",
    r"\b(?:känner\s+mig\s+)?(?:inte\s+trygg|otrygg|rädd)\b",
    r"\bhar\s+ont\b",
    r"\b(?:föll|ramlade|ramlat)\b",
    r"\b(?:behöver\s+hjälp|hjälp\s+mig)\b",
))


def _resident_distress_phrase(text: str) -> Optional[str]:
    normalized = " ".join(text.split())
    for pattern in _RESIDENT_DISTRESS_PATTERNS:
        match = pattern.search(normalized)
        if match is not None:
            return match.group(0)
    return None


@dataclass(frozen=True)
class StreamingResult:
    event_id: str
    outcome: WorkflowOutcome
    assessment: Assessment
    voice_prompt: Optional[VoicePrompt] = None
    voice_reply: Optional[CorrelatedVoiceReply] = None
    proposal: Optional[ActionProposal] = None
    triage_passes: Tuple[ActionProposal, ...] = ()
    decision: Optional[GuardianDecision] = None
    receipt: Optional[DispatchReceipt] = None


@dataclass
class PendingConversation:
    event: InputEvent
    assessment: Assessment
    prompt: VoicePrompt
    deadline: float
    timeout_seconds: float
    reply: Optional[CorrelatedVoiceReply] = None
    audio_hash: Optional[str] = None


@dataclass(frozen=True)
class AnomalyStreak:
    signature: Tuple[str, ...]
    count: int
    active: bool = False


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
        sensor_quality_monitor: Optional[SensorQualityMonitor] = None,
        *,
        debounce_readings: int = 2,
        voice_timeout_seconds: Optional[float] = None,
        critical_voice_timeout_seconds: Optional[float] = None,
        moderate_voice_timeout_seconds: Optional[float] = None,
        default_voice_timeout_seconds: float = 300.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if isinstance(debounce_readings, bool) or not isinstance(debounce_readings, int) or debounce_readings < 1:
            raise ValueError("debounce_readings must be a positive integer")
        critical_voice_timeout_seconds = self._read_timeout(
            "AURA_REPLY_TIMEOUT_CRITICAL_S", critical_voice_timeout_seconds, 120.0
        )
        moderate_voice_timeout_seconds = self._read_timeout(
            "AURA_REPLY_TIMEOUT_MODERATE_S", moderate_voice_timeout_seconds, 600.0
        )
        for name, value in (
            ("critical_voice_timeout_seconds", critical_voice_timeout_seconds),
            ("moderate_voice_timeout_seconds", moderate_voice_timeout_seconds),
            ("default_voice_timeout_seconds", default_voice_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if voice_timeout_seconds is not None and (
            isinstance(voice_timeout_seconds, bool)
            or not isinstance(voice_timeout_seconds, (int, float))
            or not math.isfinite(voice_timeout_seconds)
            or voice_timeout_seconds <= 0
        ):
            raise ValueError("voice_timeout_seconds must be finite and positive")
        self._supervisor = supervisor
        self._voice_responder = voice_responder
        self._triage = triage
        self._guardian = guardian
        self._dispatcher = dispatcher
        self._sensor_validator = sensor_validator or SensorEventValidator()
        self._sensor_quality_monitor = sensor_quality_monitor or SensorQualityMonitor()
        self._debounce_readings = debounce_readings
        self._processed: Dict[str, StreamingResult] = {}
        self._pending: Dict[str, PendingConversation] = {}
        self._inputs: Dict[str, InputEvent] = {}
        self._anomaly_streaks: Dict[str, AnomalyStreak] = {}
        self._clock = clock
        self._legacy_voice_timeout_seconds = voice_timeout_seconds
        self._critical_voice_timeout_seconds = float(critical_voice_timeout_seconds)
        self._moderate_voice_timeout_seconds = float(moderate_voice_timeout_seconds)
        self._default_voice_timeout_seconds = float(default_voice_timeout_seconds)
        self._lock = RLock()

    def start(self, event: InputEvent) -> StreamingResult:
        """Assess once and queue a device prompt without inventing a resident reply."""
        with self._lock:
            previous = self._processed.get(event.event_id)
            if previous is not None:
                if event.event_id in self._inputs and self._inputs[event.event_id] != event:
                    raise ValueError("event_id is already bound to different sensor data")
                self._expire_conversations()
                return self._processed[event.event_id]
            event = deepcopy(event)
            quality_findings = self._sensor_quality_monitor.inspect(event)
            if quality_findings:
                self._anomaly_streaks.pop(event.subject_id, None)
                result = self._route_sensor_fault(event, quality_findings)
            else:
                self._sensor_validator.validate(event)
                assessment = self._supervisor(event)
                if not assessment.is_anomaly and not assessment.requires_review:
                    self._anomaly_streaks.pop(event.subject_id, None)
                    result = StreamingResult(event.event_id, WorkflowOutcome.NO_ACTION, assessment)
                elif drop_reason := self._debounce_drop_reason(event, assessment):
                    assessment = replace(assessment, drop_reason=drop_reason)
                    result = StreamingResult(event.event_id, WorkflowOutcome.DROPPED, assessment)
                else:
                    proposal = self._run_triage(event, assessment, TriageStage.INITIAL)
                    decision = self._guardian(event, assessment, proposal)
                    if decision.status.value == "approve" and proposal.action_type == ActionType.ASK_RESIDENT.value:
                        text = proposal.parameters.get("question_sv")
                        if not isinstance(text, str) or not text.strip():
                            text = SensorAnomalyPromptBuilder().build(event, assessment)
                        else:
                            text = text.strip()
                        timeout_seconds = self._voice_timeout_for(assessment)
                        prompt = VoicePrompt(
                            conversation_id=uuid4().hex,
                            event_id=event.event_id,
                            text=text,
                            speaker_receipt=SpeakerReceipt(text, "resident_device_pending", False),
                        )
                        self._pending[prompt.conversation_id] = PendingConversation(
                            event,
                            assessment,
                            prompt,
                            self._clock() + timeout_seconds,
                            timeout_seconds,
                        )
                        result = StreamingResult(
                            event.event_id,
                            WorkflowOutcome.AWAITING_RESIDENT,
                            assessment,
                            voice_prompt=prompt,
                            proposal=proposal,
                            triage_passes=(proposal,),
                            decision=decision,
                        )
                    elif decision.status.value == "approve" and proposal.action_type == ActionType.DISMISS.value:
                        receipt = self._dispatcher(event, proposal, decision)
                        result = StreamingResult(
                            event.event_id,
                            WorkflowOutcome.DISMISSED
                            if receipt.status is DispatchStatus.SUCCEEDED
                            else WorkflowOutcome.DISPATCH_FAILED,
                            assessment,
                            proposal=proposal,
                            triage_passes=(proposal,),
                            decision=decision,
                            receipt=receipt,
                        )
                    elif decision.status.value == "approve":
                        receipt = self._dispatcher(event, proposal, decision)
                        result = StreamingResult(
                            event.event_id,
                            WorkflowOutcome.DISPATCHED
                            if receipt.status is DispatchStatus.SUCCEEDED
                            else WorkflowOutcome.DISPATCH_FAILED,
                            assessment,
                            proposal=proposal,
                            triage_passes=(proposal,),
                            decision=decision,
                            receipt=receipt,
                        )
                    else:
                        result = StreamingResult(
                            event.event_id,
                            WorkflowOutcome.HELD
                            if decision.status.value == "hold"
                            else WorkflowOutcome.DENIED,
                            assessment,
                            proposal=proposal,
                            triage_passes=(proposal,),
                            decision=decision,
                        )
            self._processed[event.event_id] = result
            self._inputs[event.event_id] = event
            return result

    def _expire_conversations(self) -> None:
        now = self._clock()
        for pending in self._pending.values():
            result = self._processed[pending.event.event_id]
            if self._is_awaiting_resident(result.outcome) and now >= pending.deadline:
                self._processed[result.event_id] = self._escalate_no_reply(pending)

    @staticmethod
    def _read_timeout(name: str, explicit: Optional[float], default: float) -> float:
        if explicit is not None:
            return float(explicit)
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError as error:
            raise ValueError(f"{name} must be a positive finite number") from error

    def _escalate_no_reply(self, pending: PendingConversation) -> StreamingResult:
        result = self._processed[pending.event.event_id]
        action_type = (
            ActionType.URGENT_VISIT.value
            if pending.assessment.severity is AssessmentSeverity.CRITICAL
            else ActionType.REMOTE_FOLLOWUP.value
        )
        proposal = ActionProposal(
            proposal_id=uuid4().hex,
            event_id=pending.event.event_id,
            action_type=action_type,
            rationale=(
                f"No resident reply arrived before the {int(pending.timeout_seconds)}-second "
                f"check-in deadline (no_resident_reply); proposing {action_type}."
            ),
            confidence=1.0,
            parameters={"reason_code": "no_resident_reply"},
            evidence_refs=tuple(
                f"supervisor.evidence.{index}"
                for index in range(len(pending.assessment.evidence))
            ),
            triage_stage=TriageStage.INITIAL,
        )
        decision = self._guardian(pending.event, pending.assessment, proposal)
        if decision.status.value == "approve":
            receipt = self._dispatcher(pending.event, proposal, decision)
            outcome = (
                WorkflowOutcome.ESCALATED_NO_REPLY
                if receipt.status is DispatchStatus.SUCCEEDED
                else WorkflowOutcome.DISPATCH_FAILED
            )
        else:
            receipt = None
            outcome = (
                WorkflowOutcome.HELD
                if decision.status.value == "hold"
                else WorkflowOutcome.DENIED
            )
        return replace(
            result,
            outcome=outcome,
            proposal=proposal,
            decision=decision,
            receipt=receipt,
        )

    def sweep_expired_conversations(self) -> Tuple[StreamingResult, ...]:
        """Escalate all conversations whose reply deadline has elapsed, once each."""
        with self._lock:
            previous = {event_id: result for event_id, result in self._processed.items()}
            self._expire_conversations()
            return tuple(
                result for event_id, result in self._processed.items()
                if previous.get(event_id) is not result
            )

    def _voice_timeout_for(self, assessment: Assessment) -> float:
        if self._legacy_voice_timeout_seconds is not None:
            return float(self._legacy_voice_timeout_seconds)
        if assessment.severity is AssessmentSeverity.CRITICAL:
            return self._critical_voice_timeout_seconds
        if assessment.severity is AssessmentSeverity.MODERATE:
            return self._moderate_voice_timeout_seconds
        return self._default_voice_timeout_seconds

    @staticmethod
    def _is_awaiting_resident(outcome: WorkflowOutcome) -> bool:
        return outcome in {
            WorkflowOutcome.AWAITING_RESIDENT,
            WorkflowOutcome.WAITING_FOR_RESIDENT,
        }

    def reply_timeout_seconds(self, conversation_id: str) -> int:
        with self._lock:
            pending = self._pending.get(conversation_id)
            if pending is None:
                raise ValueError("No pending resident conversation")
            return int(pending.timeout_seconds)

    def remaining_reply_seconds(self, conversation_id: str) -> int:
        with self._lock:
            pending = self._pending.get(conversation_id)
            if pending is None:
                raise ValueError("No pending resident conversation")
            return max(0, math.ceil(pending.deadline - self._clock()))

    def _run_triage(
        self,
        event: InputEvent,
        assessment: Assessment,
        stage: Optional[TriageStage],
    ) -> ActionProposal:
        if isinstance(self._triage, TriageAgent) and stage is not None:
            return self._triage.run(event, assessment, stage=stage)
        return self._triage(event, assessment)

    def _debounce_drop_reason(self, event: InputEvent, assessment: Assessment) -> Optional[str]:
        if not assessment.is_anomaly:
            self._anomaly_streaks.pop(event.subject_id, None)
            return None
        if assessment.severity is AssessmentSeverity.CRITICAL:
            self._anomaly_streaks.pop(event.subject_id, None)
            return None

        fields = tuple(sorted({
            str(item.details["field"])
            for item in assessment.evidence
            if isinstance(item.details.get("field"), str)
        }))
        signature = (event.source, *(fields or ("unspecified",)))
        previous = self._anomaly_streaks.get(event.subject_id)
        if previous is not None and previous.signature == signature:
            if previous.active:
                return "persistent_anomaly_already_in_progress"
            count = previous.count + 1
        else:
            count = 1

        active = count >= self._debounce_readings
        self._anomaly_streaks[event.subject_id] = AnomalyStreak(signature, count, active)
        if not active:
            return "awaiting_persistence"
        return None

    def pending_prompts(self, subject_id: str) -> Tuple[VoicePrompt, ...]:
        with self._lock:
            self._expire_conversations()
            return tuple(
                pending.prompt for pending in self._pending.values()
                if pending.event.subject_id == subject_id
                and self._is_awaiting_resident(self._processed[pending.event.event_id].outcome)
            )

    def results(self) -> Tuple[StreamingResult, ...]:
        with self._lock:
            self._expire_conversations()
            return tuple(self._processed.values())

    def _bound_conversation(self, event_id: str, subject_id: str, conversation_id: str) -> PendingConversation:
        pending = self._pending.get(conversation_id)
        if pending is None or pending.event.event_id != event_id or pending.event.subject_id != subject_id:
            raise ValueError("Reply does not match an originating resident event and conversation")
        self._expire_conversations()
        result = self._processed[event_id]
        if result.outcome == WorkflowOutcome.VOICE_EXPIRED:
            raise ValueError("Resident conversation has expired; no action was dispatched")
        if not self._is_awaiting_resident(result.outcome) and result.voice_reply is None:
            raise ValueError("Resident conversation is no longer awaiting a reply")
        return pending

    def record_playback(
        self, *, event_id: str, subject_id: str, conversation_id: str,
        provider: str, status: str,
    ) -> StreamingResult:
        """Record device-reported playback; never authorize or continue the care workflow."""
        if provider not in {"browser_speech", "gtts"}:
            raise ValueError("Unsupported playback provider")
        if status not in {"started", "completed", "stopped", "failed", "blocked"}:
            raise ValueError("Unsupported playback status")
        with self._lock:
            pending = self._bound_conversation(event_id, subject_id, conversation_id)
            previous = self._processed[event_id]
            if not self._is_awaiting_resident(previous.outcome) and previous.voice_reply is None:
                raise ValueError("Resident conversation is no longer awaiting a reply")
            if pending.prompt.speaker_receipt.delivered:
                return previous
            pending.prompt = replace(
                pending.prompt,
                speaker_receipt=SpeakerReceipt(
                    pending.prompt.text, f"{provider}_{status}", status == "completed"
                ),
            )
            result = replace(previous, voice_prompt=pending.prompt)
            self._processed[event_id] = result
            return result

    def resume_audio(
        self,
        *,
        event_id: str,
        subject_id: str,
        conversation_id: str,
        recording: AudioRecording,
        transcriber: VoiceTranscriber,
        signal_analyzer: VocalSignalAnalyzer,
    ) -> StreamingResult:
        """Use real captured audio once and continue the original assessed event."""
        with self._lock:
            pending = self._bound_conversation(event_id, subject_id, conversation_id)
            audio_hash = sha256(recording.audio).hexdigest()
            if pending.audio_hash is not None and pending.audio_hash != audio_hash:
                raise ValueError("This conversation already has a transcribed recording; retry the same recording")
            previous = self._processed[event_id]
            if not self._is_awaiting_resident(previous.outcome):
                if previous.voice_reply is not None:
                    return previous
                raise ValueError("Resident conversation is no longer awaiting a reply")
            if pending.reply is None:
                analysis = signal_analyzer.analyze(
                    recording, event_id=event_id, conversation_id=conversation_id
                )
                if analysis.event_id != event_id or analysis.conversation_id != conversation_id:
                    raise ValueError("Audio analysis does not match the resident conversation")
                if analysis.features.get("silence_ratio", 0) >= 0.99:
                    raise ValueError("Recording is silent; please record a spoken reply")
                transcript = transcriber.transcribe(recording, language="sv")
                if not transcript.text.strip():
                    raise ValueError("No speech was transcribed; please record again")
                pending.reply = CorrelatedVoiceReply(event_id, conversation_id, transcript, analysis)
                pending.audio_hash = audio_hash
            result = self._finish(pending.event, pending.assessment, pending.prompt, pending.reply)
            self._processed[event_id] = result
            return result

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
        quality_findings = self._sensor_quality_monitor.inspect(event)
        if quality_findings:
            self._anomaly_streaks.pop(event.subject_id, None)
            return self._route_sensor_fault(event, quality_findings)
        self._sensor_validator.validate(event)
        assessment = self._supervisor(event)
        if not assessment.is_anomaly and not assessment.requires_review:
            self._anomaly_streaks.pop(event.subject_id, None)
            return StreamingResult(
                event_id=event.event_id,
                outcome=WorkflowOutcome.NO_ACTION,
                assessment=assessment,
            )
        if drop_reason := self._debounce_drop_reason(event, assessment):
            return StreamingResult(
                event_id=event.event_id,
                outcome=WorkflowOutcome.DROPPED,
                assessment=replace(assessment, drop_reason=drop_reason),
            )

        voice_prompt, voice_reply = self._voice_responder.ask_and_capture(event, assessment)
        if (
            voice_prompt.event_id != event.event_id
            or voice_reply.event_id != event.event_id
            or voice_prompt.conversation_id != voice_reply.conversation_id
        ):
            raise ValueError("voice response does not correlate to the sensor event and prompt")

        return self._finish(
            event, assessment, voice_prompt, voice_reply, explicit_stage=False
        )

    def _route_sensor_fault(
        self, event: InputEvent, findings: Tuple[SensorQualityFinding, ...]
    ) -> StreamingResult:
        evidence = tuple(
            EvidenceItem(
                source="sensor_quality",
                description=finding.description,
                details=dict(finding.details),
            )
            for finding in findings
        )
        assessment = Assessment(
            event_id=event.event_id,
            is_anomaly=False,
            summary="Sensor quality fault detected; maintenance review is required.",
            confidence=1.0,
            evidence=evidence,
            requires_review=True,
            severity=AssessmentSeverity.INFO,
        )
        proposal = ActionProposal(
            proposal_id=uuid4().hex,
            event_id=event.event_id,
            action_type=ActionType.MAINTENANCE_TICKET.value,
            rationale="Inspect the sensor or its data feed; this is not a resident-visit request.",
            confidence=1.0,
            parameters={"fault_codes": [finding.code for finding in findings]},
            evidence_refs=tuple(
                f"supervisor.evidence.{index}" for index in range(len(evidence))
            ),
            triage_stage=TriageStage.INITIAL,
        )
        decision = self._guardian(event, assessment, proposal)
        if decision.status.value == "approve":
            receipt = self._dispatcher(event, proposal, decision)
            outcome = (
                WorkflowOutcome.SENSOR_FAULT
                if receipt.status is DispatchStatus.SUCCEEDED
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
            proposal=proposal,
            decision=decision,
            receipt=receipt,
        )

    def _finish(
        self, event: InputEvent, assessment: Assessment,
        voice_prompt: VoicePrompt, voice_reply: CorrelatedVoiceReply,
        *, explicit_stage: bool = True,
    ) -> StreamingResult:
        enriched_event = replace(
            event,
            transcript=voice_reply.transcript,
            observations={
                **event.observations,
                "vocal_signal_features": dict(voice_reply.signal_analysis.features),
            },
        )
        distress_phrase = _resident_distress_phrase(voice_reply.transcript.text)
        triage_assessment = assessment
        if distress_phrase is not None:
            triage_assessment = replace(
                assessment,
                requires_review=True,
                severity=AssessmentSeverity.CRITICAL,
                evidence=assessment.evidence + (
                    EvidenceItem(
                        source="resident_reply_escalation",
                        description="Resident reply matched a configured explicit distress phrase.",
                        details={"reason_code": "resident_reply_distress"},
                    ),
                ),
            )
        proposal = replace(
            self._run_triage(
                enriched_event,
                triage_assessment,
                TriageStage.POST_REPLY if explicit_stage else None,
            ),
            triage_stage=TriageStage.POST_REPLY,
        )
        if distress_phrase is not None:
            proposal = replace(
                proposal,
                action_type=ActionType.REQUEST_HUMAN_REVIEW.value,
                rationale=(
                    f'Resident reply included "{distress_phrase}"; immediate human review is requested.'
                ),
                confidence=1.0,
                parameters={
                    **proposal.parameters,
                    "priority": "Urgent",
                    "reason_code": "resident_reply_distress",
                },
            )
        previous_result = self._processed.get(event.event_id)
        triage_passes = (
            previous_result.triage_passes if previous_result is not None else ()
        ) + (proposal,)
        decision = self._guardian(enriched_event, triage_assessment, proposal)

        if decision.status.value == "approve":
            if proposal.action_type == ActionType.DISMISS.value:
                receipt = self._dispatcher(enriched_event, proposal, decision)
                outcome = (
                    WorkflowOutcome.DISMISSED
                    if receipt.status is DispatchStatus.SUCCEEDED
                    else WorkflowOutcome.DISPATCH_FAILED
                )
            else:
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
            triage_passes=triage_passes,
            decision=decision,
            receipt=receipt,
        )