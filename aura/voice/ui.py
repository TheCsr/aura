"""Streamlit workbench for exercising mock speaker and microphone components."""

from pathlib import Path
import sys

import streamlit as st

if __package__ in (None, ""):
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from aura.voice.adapters.streamlit_microphone import StreamlitMicrophone
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer
from aura.voice.session import ResidentVoiceSession
from aura.voice.testing import MockSpeaker


st.set_page_config(page_title="AURA Voice I/O", page_icon="🎙️", layout="centered")
st.title("Resident Voice I/O")
st.caption("Mock speaker and transcription; microphone audio is captured in this browser session.")

if "aura_mock_speaker" not in st.session_state:
    st.session_state.aura_mock_speaker = MockSpeaker()
if "aura_mock_transcriber" not in st.session_state:
    st.session_state.aura_mock_transcriber = FakeSpeechToTextAdapter()
if st.session_state.get("aura_voice_session_version") != 2:
    st.session_state.aura_voice_session = ResidentVoiceSession(
        st.session_state.aura_mock_speaker,
        st.session_state.aura_mock_transcriber,
        signal_analyzer=PcmWavSignalAnalyzer(),
    )
    st.session_state.aura_voice_session_version = 2

transcriber = st.session_state.aura_mock_transcriber
voice_session = st.session_state.aura_voice_session

speaker_column, microphone_column = st.columns(2)

with speaker_column:
    st.subheader("Speaker")
    event_id = st.text_input(
        "Related sensor event ID",
        value="sensor-event-demo-001",
        key="aura_voice_event_id",
    )
    prompt = st.text_area(
        "Prompt text",
        value="Hej, jag märker att dörren är öppen. Är allt bra?",
        height=120,
        key="aura_voice_prompt",
    )
    if st.button("Send prompt to mock speaker", type="primary", use_container_width=True):
        try:
            voice_prompt = voice_session.ask(event_id, prompt)
            st.session_state.aura_last_voice_prompt = voice_prompt
        except ValueError as error:
            st.error(str(error))

    last_prompt = st.session_state.get("aura_last_voice_prompt")
    if last_prompt is not None:
        st.info("Mock speaker received this text. No physical speaker or TTS service is connected.")
        st.write(last_prompt.text)
        st.caption(
            f"Event: {last_prompt.event_id} · Conversation: {last_prompt.conversation_id}"
        )

with microphone_column:
    st.subheader("Microphone")
    pending_prompts = voice_session.pending_prompts
    if pending_prompts:
        prompt_by_conversation = {
            voice_prompt.conversation_id: voice_prompt for voice_prompt in pending_prompts
        }
        conversation_id = st.selectbox(
            "Response belongs to",
            options=tuple(prompt_by_conversation),
            format_func=lambda value: (
                f"{prompt_by_conversation[value].event_id} · {value}"
            ),
        )
        selected_prompt = prompt_by_conversation[conversation_id]
        st.caption(f"Prompt: {selected_prompt.text}")
        microphone = StreamlitMicrophone(key=f"resident_voice_{conversation_id}")
        recording = microphone.capture()
        if recording is not None:
            st.audio(recording.audio, format=recording.content_type)
            st.caption(f"Captured {len(recording.audio):,} bytes from the browser microphone.")
            mock_transcript = st.text_area(
                "Mock transcript",
                value="Jag mår bra, tack.",
                height=80,
                help="This text stands in for KB-Whisper output; it is not inferred from the recording.",
                key=f"aura_mock_transcript_text_{conversation_id}",
            )
            language = st.selectbox(
                "Transcript language", options=("sv", "en"), index=0,
                key=f"aura_voice_language_{conversation_id}",
            )
            if st.button(
                "Process mock transcript",
                key=f"process_voice_{conversation_id}",
                use_container_width=True,
            ):
                try:
                    transcriber.set_transcript(mock_transcript)
                    correlated = voice_session.process_reply(
                        conversation_id,
                        recording,
                        language=language,
                    )
                    st.session_state.aura_last_correlated_transcript = correlated
                except ValueError as error:
                    st.error(str(error))
    else:
        st.caption("Send a speaker prompt to create a pending response conversation.")

correlated = st.session_state.get("aura_last_correlated_transcript")
if correlated is not None:
    st.divider()
    st.subheader("Transcription result")
    st.write(correlated.transcript.text)
    st.caption(
        f"Event: {correlated.event_id} · Conversation: {correlated.conversation_id}"
    )
    st.caption(
        f"Source: {correlated.transcript.source} · Language: {correlated.transcript.language} "
        f"· Model: {correlated.transcript.model}"
    )
    st.subheader("Acoustic signal features")
    st.caption(
        "Waveform measurements only. These are not pain biomarkers or a medical assessment."
    )
    st.json(correlated.signal_analysis.features)
    st.caption(
        f"Analyzer: {correlated.signal_analysis.source} · "
        f"Event: {correlated.signal_analysis.event_id} · "
        f"Conversation: {correlated.signal_analysis.conversation_id}"
    )