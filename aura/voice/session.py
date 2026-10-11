"""Application service coordinating speaker output and resident response capture."""

from typing import Callable, Dict, Optional, Tuple
from uuid import uuid4

from ..contracts import Transcript
from .contracts import (
    AudioRecording,
    CorrelatedTranscript,
    CorrelatedVoiceReply,
    VoicePrompt,
    VocalSignalAnalysis,
)
from .ports import Speaker, VocalSignalAnalyzer, VoiceTranscriber


class ResidentVoiceSession:
    def __init__(
        self,
        speaker: Speaker,
        transcriber: VoiceTranscriber,
        conversation_id_factory: Callable[[], str] = lambda: uuid4().hex,
        signal_analyzer: Optional[VocalSignalAnalyzer] = None,
    ) -> None:
        self._speaker = speaker
        self._transcriber = transcriber
        self._conversation_id_factory = conversation_id_factory
        self._signal_analyzer = signal_analyzer
        self._pending_prompts: Dict[str, VoicePrompt] = {}

    @property
    def pending_prompts(self) -> Tuple[VoicePrompt, ...]:
        return tuple(self._pending_prompts.values())

    def ask(self, event_id: str, prompt: str) -> VoicePrompt:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        conversation_id = self._conversation_id_factory()
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id factory must return a non-empty string")
        if conversation_id in self._pending_prompts:
            raise ValueError("conversation_id must be unique among pending prompts")
        text = prompt.strip()
        receipt = self._speaker.speak(text)
        voice_prompt = VoicePrompt(
            conversation_id=conversation_id,
            event_id=event_id,
            text=text,
            speaker_receipt=receipt,
        )
        self._pending_prompts[conversation_id] = voice_prompt
        return voice_prompt

    def transcribe_reply(
        self,
        conversation_id: str,
        recording: AudioRecording,
        *,
        language: str = "sv",
    ) -> CorrelatedTranscript:
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a non-empty string")
        prompt = self._pending_prompts.get(conversation_id)
        if prompt is None:
            raise ValueError("No pending prompt found for this conversation_id")
        transcript = self._transcriber.transcribe(recording, language=language)
        if not isinstance(transcript, Transcript):
            raise ValueError("voice transcriber must return a Transcript")
        correlated = CorrelatedTranscript(
            conversation_id=prompt.conversation_id,
            event_id=prompt.event_id,
            transcript=transcript,
        )
        del self._pending_prompts[conversation_id]
        return correlated

    def cancel_prompt(self, conversation_id: str) -> bool:
        return self._pending_prompts.pop(conversation_id, None) is not None

    def process_reply(
        self,
        conversation_id: str,
        recording: AudioRecording,
        *,
        language: str = "sv",
    ) -> CorrelatedVoiceReply:
        if self._signal_analyzer is None:
            raise ValueError("No vocal signal analyzer is configured")
        prompt = self._pending_prompts.get(conversation_id)
        if prompt is None:
            raise ValueError("No pending prompt found for this conversation_id")

        analysis = self._signal_analyzer.analyze(
            recording,
            event_id=prompt.event_id,
            conversation_id=prompt.conversation_id,
        )
        if not isinstance(analysis, VocalSignalAnalysis):
            raise ValueError("vocal signal analyzer must return VocalSignalAnalysis")
        if (
            analysis.event_id != prompt.event_id
            or analysis.conversation_id != prompt.conversation_id
        ):
            raise ValueError("vocal signal analysis does not match the pending conversation")

        correlated_transcript = self.transcribe_reply(
            conversation_id,
            recording,
            language=language,
        )
        return CorrelatedVoiceReply(
            event_id=prompt.event_id,
            conversation_id=prompt.conversation_id,
            transcript=correlated_transcript.transcript,
            signal_analysis=analysis,
        )