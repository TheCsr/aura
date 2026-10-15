"""Standalone Resident In-Home Device Terminal (Streamlit App).

This app acts as the elderly person's in-home hardware device:
- Plays prompts with a Swedish browser voice or explicit Google Translate demo fallback.
- Captures resident audio for backend-only Orange KB-Whisper transcription.
- Submits resident voice response back to the AURA Multi-Agent pipeline.
"""

import base64
from io import BytesIO
import os
from pathlib import Path
import sys

from gtts import gTTS
import requests
import streamlit as st
import streamlit.components.v1 as components

# Ensure repository root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load environment variables from .env
def _load_env() -> None:
    env_file = _PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as err:
            pass

_load_env()

API_BASE_URL = os.environ.get("AURA_API_URL", "http://localhost:8000").rstrip("/")
SUBJECT_ID = os.environ.get("AURA_SUBJECT_ID", "synthetic-resident-1").strip()
speaker_component = components.declare_component(
    "aura_resident_speaker",
    path=str(_PROJECT_ROOT / "aura" / "voice" / "speaker_frontend"),
)

st.set_page_config(
    page_title="Resident Check-in",
    layout="centered",
)

st.title("Resident check-in")
st.caption("AURA home-care demo")

# Function to synthesize Swedish speech via Google Translate TTS
def generate_tts_audio_bytes(text: str) -> bytes:
    buf = BytesIO()
    tts = gTTS(text=text.strip(), lang="sv", slow=False, timeout=(5, 20))
    tts.write_to_fp(buf)
    buf.seek(0)
    return buf.getvalue()


# Function to poll backend for agent prompt
def fetch_agent_prompt():
    try:
        res = requests.get(
            f"{API_BASE_URL}/api/device/prompt",
            params={"subject_id": SUBJECT_ID}, timeout=5,
        )
        res.raise_for_status()
        return res.json()
    except (requests.RequestException, ValueError):
        return None


# Function to send response back to Aura Agent Pipeline
def send_response_to_aura(prompt, audio_bytes: bytes):
    res = requests.post(
        f"{API_BASE_URL}/api/device/respond",
        data={key: prompt[key] for key in ("subject_id", "event_id", "conversation_id")},
        files={"audio_file": ("reply.wav", audio_bytes, "audio/wav")},
        timeout=(10, 180),
    )
    return res


def render_speaker(prompt):
    conversation_id = prompt["conversation_id"] if prompt else None
    if st.session_state.get("speaker_conversation") != conversation_id:
        st.session_state["speaker_conversation"] = conversation_id
        st.session_state.pop("speaker_audio", None)
        st.session_state.pop("speaker_fallback_error", None)
    receipt = speaker_component(
        prompt=prompt,
        audio_base64=st.session_state.get("speaker_audio"),
        fallback_error=st.session_state.get("speaker_fallback_error", False),
        key="resident-speaker",
        default=None,
    )
    if not prompt or not isinstance(receipt, dict):
        return
    if any(receipt.get(key) != prompt[key] for key in ("subject_id", "event_id", "conversation_id")):
        return
    notification_id = receipt.get("notification_id")
    if not notification_id or notification_id == st.session_state.get("speaker_notification"):
        return
    if receipt.get("action") == "fallback":
        st.session_state["speaker_notification"] = notification_id
        try:
            audio = generate_tts_audio_bytes(prompt["text"])
            st.session_state["speaker_audio"] = base64.b64encode(audio).decode("ascii")
            st.session_state["speaker_fallback_error"] = False
        except Exception:
            st.session_state["speaker_fallback_error"] = True
        st.rerun(scope="fragment")
    elif receipt.get("action") == "playback":
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/device/playback",
                json={key: receipt.get(key) for key in ("subject_id", "event_id", "conversation_id", "provider", "status")},
                timeout=5,
            )
            if response.ok or response.status_code == 409:
                st.session_state["speaker_notification"] = notification_id
            else:
                st.warning("Playback status could not be confirmed by the server.")
        except requests.RequestException:
            st.warning("Playback status could not be confirmed by the server.")


@st.fragment(run_every="3s")
def resident_conversation():
    response = fetch_agent_prompt()
    prompt = response.get("prompt") if response else None
    render_speaker(prompt)
    if response is None:
        st.error("Cannot connect to AURA. Please try again shortly.")
        return
    previous = st.session_state.get("sent_result")
    if previous:
        st.success("Your reply was received.")
        st.write(previous["transcript"])
    if prompt is None:
        st.info("No check-in is waiting right now.")
        return
    conversation_id = prompt["conversation_id"]
    st.subheader("Message")
    with st.container(border=True):
        st.text(prompt["text"])
        remaining_seconds = prompt.get("remaining_reply_seconds")
        if isinstance(remaining_seconds, int) and not isinstance(remaining_seconds, bool):
            minutes, seconds = divmod(max(0, remaining_seconds), 60)
            st.caption(f"Time remaining: {minutes:02d}:{seconds:02d}")
    st.subheader("Your reply")
    if not response.get("whisper_configured"):
        st.warning("The reply service is not configured on the server.")
        return
    recording = st.audio_input("Record your reply", sample_rate=16000, key=f"record-{conversation_id}")
    if recording is not None and st.button("Send reply", key=f"send-{conversation_id}"):
        with st.spinner("Transcribing and sending your reply..."):
            try:
                reply = send_response_to_aura(prompt, recording.getvalue())
                payload = reply.json()
                if reply.ok:
                    st.session_state["sent_result"] = payload
                    st.rerun(scope="fragment")
                else:
                    st.error(payload.get("detail", "Your reply could not be sent. Please try again."))
            except (requests.RequestException, ValueError):
                st.error("Your reply could not be sent. Please retry the same recording.")


resident_conversation()

# Demo triggers in sidebar
with st.sidebar:
    st.caption(f"Demo resident: {SUBJECT_ID}")
    with st.expander("Demo alerts"):
        for label, scenario in (
            ("Critical cold", "critical-cold"),
            ("Door open", "door-open"),
        ):
            if st.button(label, key=f"trigger-{scenario}", use_container_width=True):
                try:
                    result = requests.post(
                        f"{API_BASE_URL}/api/device/trigger-prompt",
                        json={"scenario": scenario},
                        timeout=5,
                    )
                    result.raise_for_status()
                    st.rerun()
                except requests.RequestException:
                    st.error("The demo alert could not be started.")
