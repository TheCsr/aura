import hashlib
import os
import time

import requests
import streamlit as st

BASE_URL = "https://kb-whisper-vdr-tieto-poc-fa09560c.caas.mandarine-business.cloud"
MODEL = "KBLab/kb-whisper-large"

st.set_page_config(page_title="Swedish Transcription")
st.title("Swedish Transcription")

api_key = os.environ.get("ORANGE_API_KEY", "").strip()
if not api_key:
    st.error("Set ORANGE_API_KEY before starting.")
    st.stop()

recording = st.audio_input("Record Swedish audio", sample_rate=16000)

if recording is None:
    st.session_state.pop("processed_recording", None)
elif recording is not None:
    audio_bytes = recording.getvalue()
    recording_id = hashlib.sha256(audio_bytes).hexdigest()

    if recording_id != st.session_state.get("processed_recording"):
        st.session_state["processed_recording"] = recording_id
        st.session_state["transcription"] = ""
        st.session_state.pop("elapsed", None)
        st.session_state.pop("error", None)

        started = time.perf_counter()
        with st.spinner("Transcribing..."):
            try:
                response = requests.post(
                    f"{BASE_URL.rstrip('/')}/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("recording.wav", audio_bytes, "audio/wav")},
                    data={
                        "model": MODEL,
                        "language": "sv",
                        "response_format": "json",
                    },
                    timeout=(15, 600),
                )
                response.raise_for_status()
                text = response.json()["text"]
                if not isinstance(text, str):
                    raise ValueError("Invalid transcription response.")

                st.session_state["transcription"] = text
                st.session_state["elapsed"] = time.perf_counter() - started
            except (requests.RequestException, ValueError, KeyError):
                st.session_state["error"] = (
                    "Transcription failed. Clear the recording and record again."
                )

if st.session_state.get("error"):
    st.error(st.session_state["error"])

st.text_area("Transcription", key="transcription", height=220)

if "elapsed" in st.session_state:
    st.caption(f"Request time: {st.session_state['elapsed']:.2f} seconds")