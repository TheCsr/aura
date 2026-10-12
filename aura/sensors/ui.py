"""Streamlit page for entering sensor values and publishing them to LavinMQ.

Run:  streamlit run aura/sensors/ui.py
Then process them with:  python -m aura.streaming.demo consume
"""

from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import pika
import streamlit as st

if __package__ in (None, ""):
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from aura.messaging.codec import EventMessageCodec
from aura.sensors.manual_input import build_sensor_event, manual_input_settings, publish_events
from aura.sensors.validation import SensorValidationError
from aura.streaming.composition import DEFAULT_SUBJECT_ID


PRESETS = {
    "Normal": {"temperature_c": 18.0, "humidity_pct": 45.0, "door_state": "closed", "motion_detected": True},
    "Door open, no motion": {"temperature_c": 18.0, "humidity_pct": 45.0, "door_state": "open", "motion_detected": False},
    "Critical cold": {"temperature_c": -20.0, "humidity_pct": 45.0, "door_state": "open", "motion_detected": False},
    "Hot and humid": {"temperature_c": 46.0, "humidity_pct": 96.0, "door_state": "closed", "motion_detected": True},
}
DOOR_STATES = ("closed", "open", "unknown")


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password is None:
        return url
    return parsed._replace(netloc=parsed.netloc.replace(f":{parsed.password}@", ":****@", 1)).geturl()


def apply_preset(name: str) -> None:
    for field, value in PRESETS[name].items():
        st.session_state[f"sensor_{field}"] = value


st.set_page_config(page_title="AURA Sensor Input", page_icon="🎛️", layout="centered")

try:
    settings = manual_input_settings()
except ValueError as error:
    st.error(f"Invalid LavinMQ settings: {error}")
    st.stop()

if "sensor_temperature_c" not in st.session_state:
    apply_preset("Normal")
    st.session_state.sensor_subject_id = DEFAULT_SUBJECT_ID
    st.session_state.sensor_history = []

st.title("🎛️ Sensor input")
st.caption(
    "Acts as the sensor device: enter a reading and publish it to LavinMQ. "
    "Run `python -m aura.streaming.demo consume` in a terminal to process it."
)
with st.expander("Broker"):
    st.code(redact_url(settings.url), language=None)
    st.caption(
        f"Exchange `{settings.exchange}` · routing key `{settings.routing_key}` · "
        f"queue `{settings.queue}`  \nSet `LAVINMQ_URL` before starting to use another broker."
    )

st.write("**Presets**")
for name, column in zip(PRESETS, st.columns(len(PRESETS))):
    column.button(name, on_click=apply_preset, args=(name,), use_container_width=True)

st.text_input(
    "Resident ID",
    key="sensor_subject_id",
    help=f"The demo consumer has a baseline for {DEFAULT_SUBJECT_ID}. "
    "Other IDs have no baseline, so the Supervisor asks for human review.",
)
left, right = st.columns(2)
left.number_input("Temperature (°C)", step=0.5, format="%.1f", key="sensor_temperature_c")
right.number_input("Humidity (%)", step=1.0, format="%.1f", key="sensor_humidity_pct")
left.radio("Door", DOOR_STATES, horizontal=True, key="sensor_door_state")
right.toggle("Motion detected", key="sensor_motion_detected")

with st.expander("Options"):
    copies = st.number_input("Copies to send", min_value=1, max_value=20, value=1, step=1)
    validate = st.toggle(
        "Validate before publishing",
        value=True,
        help="Valid ranges: temperature -80 to 100 °C, humidity 0 to 100 %. Turn this off "
        "to send an invalid reading on purpose; the consumer will reject it instead.",
    )

try:
    events = [
        build_sensor_event(
            subject_id=st.session_state.sensor_subject_id,
            temperature_c=st.session_state.sensor_temperature_c,
            humidity_pct=st.session_state.sensor_humidity_pct,
            door_state=st.session_state.sensor_door_state,
            motion_detected=st.session_state.sensor_motion_detected,
        )
        for _ in range(int(copies))
    ]
except ValueError as error:
    events = []
    st.error(str(error))

if st.button("📤 Publish to LavinMQ", type="primary", use_container_width=True, disabled=not events):
    try:
        published = publish_events(settings, events, validate=validate)
    except SensorValidationError as error:
        st.error(f"Not published, the reading is invalid: {error}")
    except (pika.exceptions.AMQPError, OSError) as error:
        st.error(
            f"Cannot reach LavinMQ at {redact_url(settings.url)} ({type(error).__name__}). "
            "Is the broker running?"
        )
    else:
        st.success(f"Published {len(published)} event(s) to `{settings.exchange}`.")
        observations = events[0].observations
        for event_id in published:
            st.session_state.sensor_history.insert(
                0,
                {
                    "sent": datetime.now().strftime("%H:%M:%S"),
                    "event_id": event_id,
                    "resident": events[0].subject_id,
                    "temp °C": observations["temperature_c"],
                    "humidity %": observations["humidity_pct"],
                    "door": observations["door_state"],
                    "motion": observations["motion_detected"],
                },
            )

if events:
    with st.expander("Message that will be sent (JSON)"):
        st.json(json.loads(EventMessageCodec.encode(events[0])))

if st.session_state.sensor_history:
    st.subheader("Published in this session")
    st.dataframe(st.session_state.sensor_history[:20], hide_index=True, use_container_width=True)
