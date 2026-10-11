"""Streamlit browser microphone adapter."""

from typing import Optional

import streamlit as st

from ..contracts import AudioRecording
from ..ports import Microphone


class StreamlitMicrophone(Microphone):
    def __init__(self, key: str = "resident_voice_recording") -> None:
        self._key = key

    def capture(self) -> Optional[AudioRecording]:
        recording = st.audio_input(
            "Record the resident's reply",
            sample_rate=16000,
            key=self._key,
        )
        if recording is None:
            return None
        audio = recording.getvalue()
        if not audio:
            return None
        return AudioRecording(
            audio=audio,
            filename=getattr(recording, "name", None) or "resident-reply.wav",
            content_type=getattr(recording, "type", None) or "audio/wav",
        )