"""Standalone Resident In-Home Device Terminal (Streamlit App).

This app acts as the elderly person's in-home hardware device:
- Plays voice prompts received from the Agent over speaker via Google Translate TTS.
- Allows recording resident audio via microphone and transcribing via Orange KB-Whisper Large.
- Submits resident voice response back to the AURA Multi-Agent pipeline.
"""

import base64
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sys
import time

from gtts import gTTS
import requests
import streamlit as st

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
KB_WHISPER_URL = os.environ.get(
    "KB_WHISPER_URL",
    "https://kb-whisper-batched-vdr-tieto-poc-fa09560c.caas.mandarine-business.cloud/",
).rstrip("/")
KB_WHISPER_MODEL = os.environ.get("KB_WHISPER_MODEL", "KBLab/kb-whisper-large")

st.set_page_config(
    page_title="AURA Hemterminal",
    page_icon="🏡",
    layout="centered",
)

st.markdown(
    """
    <style>
    .stApp {
        background-color: #080c14;
        color: #f1f5f9;
    }
    .header-box {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 24px;
    }
    .prompt-box {
        background: rgba(14, 28, 48, 0.7);
        border: 1px solid rgba(56, 189, 248, 0.3);
        border-radius: 16px;
        padding: 24px;
        text-align: center;
        margin: 16px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Header
st.markdown(
    """
    <div class="header-box">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <h2 style="margin:0; font-weight:300; letter-spacing:0.5px;">🏡 AURA Hemterminal</h2>
                <span style="font-size:12px; color:#94a3b8;">Trygghetsassistent för äldreboende & hemvård</span>
            </div>
            <div style="text-align:right;">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color:#10b981; margin-right:6px;"></span>
                <span style="font-size:12px; color:#10b981; font-weight:500;">Ansluten</span>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Function to synthesize Swedish speech via Google Translate TTS
def generate_tts_audio_bytes(text: str) -> bytes:
    buf = BytesIO()
    tts = gTTS(text=text.strip(), lang="sv", slow=False)
    tts.write_to_fp(buf)
    buf.seek(0)
    return buf.getvalue()


# Function to transcribe via Orange KB-Whisper API
def transcribe_audio_kb_whisper(audio_bytes: bytes) -> str:
    api_key = os.environ.get("ORANGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ORANGE_API_KEY saknas i .env filen.")

    response = requests.post(
        f"{KB_WHISPER_URL}/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("resident_recording.wav", audio_bytes, "audio/wav")},
        data={
            "model": KB_WHISPER_MODEL,
            "language": "sv",
            "response_format": "json",
        },
        timeout=(15, 60),
    )
    response.raise_for_status()
    result = response.json()
    text = result.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Ogiltigt svar från transkriberingstjänsten.")
    return text.strip()


# Function to poll backend for agent prompt
def fetch_agent_prompt():
    try:
        res = requests.get(f"{API_BASE_URL}/api/device/prompt", timeout=3)
        if res.ok:
            return res.json().get("prompt")
    except Exception:
        pass
    return None


# Function to send response back to Aura Agent Pipeline
def send_response_to_aura(transcript: str, audio_bytes: bytes = None):
    try:
        files = {}
        if audio_bytes:
            files["audio_file"] = ("reply.wav", audio_bytes, "audio/wav")
        data = {"text_response": transcript}
        res = requests.post(
            f"{API_BASE_URL}/api/device/respond",
            data=data,
            files=files if files else None,
            timeout=15,
        )
        if res.ok:
            return res.json()
    except Exception as e:
        st.error(f"Kunde inte kontakta AURA Dashboard: {e}")
    return None


# Fetch prompt
prompt_data = fetch_agent_prompt()

default_text = "Hej. Vi har märkt en avvikelse i sensorerna hemma hos dig. Är allt bra? Berätta gärna om du behöver hjälp."
agent_message = prompt_data.get("text") if prompt_data else default_text

# 1. HÖGTALARE (Agent Speaker Output)
st.subheader("🔊 Högtalare från vårdassistenten")
st.markdown(
    f"""
    <div class="prompt-box">
        <p style="font-size:18px; font-weight:300; margin:0; line-height:1.6; color:#e0f2fe;">
            "{agent_message}"
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Play TTS audio button & player
col_audio1, col_audio2 = st.columns([1, 2])
with col_audio1:
    if st.button("▶️ Läs upp meddelande (Google TTS)", use_container_width=True):
        st.session_state["tts_audio"] = generate_tts_audio_bytes(agent_message)

if "tts_audio" not in st.session_state:
    st.session_state["tts_audio"] = generate_tts_audio_bytes(agent_message)

st.audio(st.session_state["tts_audio"], format="audio/mp3", autoplay=False)

st.divider()

# 2. MIKROFON (Resident Voice Recording)
st.subheader("🎙️ Tala in ditt svar")
st.caption("Tryck på mikrofonknappen nedan för att spela in ditt svar till vårdteamet:")

recording = st.audio_input("Spela in röstsvar (svenska)")

if recording is not None:
    audio_bytes = recording.getvalue()
    rec_hash = hashlib.sha256(audio_bytes).hexdigest()

    if rec_hash != st.session_state.get("last_rec_hash"):
        st.session_state["last_rec_hash"] = rec_hash
        st.session_state["transcribed_text"] = ""
        st.session_state["sent_result"] = None

        with st.spinner("Transkriberar röst via KB-Whisper Large..."):
            try:
                transcript = transcribe_audio_kb_whisper(audio_bytes)
                st.session_state["transcribed_text"] = transcript
                st.success(f"Transkriberat: **\"{transcript}\"**")

                # Send directly to Aura workflow
                with st.spinner("Skickar svar till AURA handläggning..."):
                    result = send_response_to_aura(transcript, audio_bytes)
                    if result:
                        st.session_state["sent_result"] = result
            except Exception as err:
                st.warning(f"KB-Whisper transkribering misslyckades ({err}). Du kan använda snabbknapparna nedan.")

# Quick response fallback buttons
st.markdown("**Eller välj ett snabbsvar med ett klick:**")
qcol1, qcol2, qcol3 = st.columns(3)

with qcol1:
    if st.button("🟢 Allt är bra", use_container_width=True):
        reply_txt = "Jag mår bra, oroa dig inte. Det var ett misstag."
        st.session_state["transcribed_text"] = reply_txt
        res = send_response_to_aura(reply_txt)
        st.session_state["sent_result"] = res

with qcol2:
    if st.button("🔴 Jag behöver hjälp", use_container_width=True):
        reply_txt = "Jag har ramlat och behöver hjälp med en gång."
        st.session_state["transcribed_text"] = reply_txt
        res = send_response_to_aura(reply_txt)
        st.session_state["sent_result"] = res

with qcol3:
    if st.button("🟡 Glömde dörren", use_container_width=True):
        reply_txt = "Dörren stod visst lite öppen, jag stänger den nu."
        st.session_state["transcribed_text"] = reply_txt
        res = send_response_to_aura(reply_txt)
        st.session_state["sent_result"] = res

# Display outcome from Agent processing
sent_result = st.session_state.get("sent_result")
if sent_result:
    st.markdown("---")
    outcome_val = sent_result.get("outcome", "ok")
    if outcome_val == "dispatched":
        st.success(
            f"✅ Svar mottaget av vårdteamet! Ärende skapat: **{sent_result.get('ticket_id')}**"
        )
    else:
        st.info(f"ℹ️ Svar registrerat (Utfall: {outcome_val})")

# Demo triggers in sidebar
with st.sidebar:
    st.subheader("⚙️ Enhetsinställningar")
    orange_key = os.environ.get("ORANGE_API_KEY", "").strip()
    if orange_key:
        st.success("🟢 ORANGE_API_KEY aktiv")
    else:
        st.warning("⚠️ ORANGE_API_KEY saknas i .env")

    st.markdown("---")
    st.subheader("⚡ Demo Larmtriggers")
    if st.button("❄️ Trigga köldlarm (-20°C)"):
        try:
            r = requests.post(f"{API_BASE_URL}/api/device/trigger-prompt", json={"scenario": "critical-cold"}, timeout=5)
            if r.ok:
                st.rerun()
        except Exception as e:
            st.error(str(e))

    if st.button("🚪 Trigga dörrlarm"):
        try:
            r = requests.post(f"{API_BASE_URL}/api/device/trigger-prompt", json={"scenario": "door-open"}, timeout=5)
            if r.ok:
                st.rerun()
        except Exception as e:
            st.error(str(e))
