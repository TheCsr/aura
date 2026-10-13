"""Composition root for the offline-ready LavinMQ streaming demo."""

from dataclasses import dataclass
from typing import Mapping, Optional

from ..agents.critical_risk_policy import CriticalRiskPolicy
from ..agents.dispatcher import ActionToolRegistry, DispatcherAgent
from ..agents.guardian import GuardianAgent
from ..agents.supervisor import SupervisorAgent
from ..llm import llm_from_environment
from ..agents.supervisor_llm import LlmAssessmentStrategy
from ..agents.triage import TriageAgent
from ..contracts import JsonValue
from ..dashboard import MockDashboardAdapter
from ..ports import ActionPolicy
from ..sensors.validation import SensorEventValidator
from ..testing import DeterministicFakeAssessmentChatModel, DeterministicFakeChatModel, InMemoryBaselineStore
from ..voice.adapters.fake_stt import FakeSpeechToTextAdapter
from ..voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer
from ..voice.testing import MockSpeaker
from .service import StreamingCareService
from .voice import ScriptedVoiceResponder, SyntheticSignalMicrophone


DEFAULT_SUBJECT_ID = "synthetic-resident-1"
DEFAULT_BASELINE: Mapping[str, JsonValue] = {
    "temperature_c": 18.0,
    "humidity_pct": 45.0,
    "door_state": "closed",
    "motion_detected": True,
}


@dataclass(frozen=True)
class StreamingDemo:
    service: StreamingCareService
    dashboard: MockDashboardAdapter


def create_streaming_demo(
    *,
    baselines: Optional[Mapping[str, Mapping[str, JsonValue]]] = None,
    resident_response: str = "Jag är okej, men jag vill gärna prata med personalen.",
    guardian_policy: Optional[ActionPolicy] = None,
) -> StreamingDemo:
    selected_baselines = (
        {DEFAULT_SUBJECT_ID: dict(DEFAULT_BASELINE)} if baselines is None else baselines
    )
    baseline_store = InMemoryBaselineStore(selected_baselines)
    real_llm = llm_from_environment()
    supervisor = SupervisorAgent(
        baseline_store,
        strategy=LlmAssessmentStrategy(real_llm or DeterministicFakeAssessmentChatModel()),
    )
    voice_responder = ScriptedVoiceResponder(
        speaker=MockSpeaker(),
        microphone=SyntheticSignalMicrophone(),
        transcriber=FakeSpeechToTextAdapter(resident_response),
        signal_analyzer=PcmWavSignalAnalyzer(),
    )
    triage = TriageAgent(real_llm or DeterministicFakeChatModel())
    guardian = GuardianAgent(policy=guardian_policy or CriticalRiskPolicy())
    dashboard = MockDashboardAdapter()
    dispatcher = DispatcherAgent(ActionToolRegistry({"request_human_review": dashboard}))
    service = StreamingCareService(
        supervisor=supervisor,
        voice_responder=voice_responder,
        triage=triage,
        guardian=guardian,
        dispatcher=dispatcher,
        sensor_validator=SensorEventValidator(),
    )
    return StreamingDemo(service=service, dashboard=dashboard)