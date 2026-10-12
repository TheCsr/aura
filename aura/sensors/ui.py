"""Streamlit page for entering sensor values and publishing them to LavinMQ.

Run:  ./start-sensor-ui.sh      (or: streamlit run aura/sensors/ui.py)

The agent consumer starts automatically inside this page and LAVINMQ_URL is
read from .env, so no other terminal is needed.
"""

from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import pika
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from aura.messaging.codec import EventMessageCodec
from aura.sensors.live_consumer import BackgroundConsumer, run_self_test
from aura.sensors.manual_input import (
    PRESETS,
    build_sensor_event,
    load_env_file,
    manual_input_settings,
    publish_events,
)
from aura.sensors.validation import SensorValidationError
from aura.streaming.composition import DEFAULT_SUBJECT_ID


DOOR_STATES = ("closed", "open", "unknown")
OUTCOME_LABELS = {
    "no_action": "⚪ no action",
    "dispatched": "🟢 ticket created",
    "held": "🟡 held",
    "denied": "🔴 denied",
    "dispatch_failed": "🔴 dispatch failed",
    "rejected": "☠️ rejected",
}


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password is None:
        return url
    return parsed._replace(netloc=parsed.netloc.replace(f":{parsed.password}@", ":****@", 1)).geturl()


def apply_preset(name: str) -> None:
    for field, value in PRESETS[name].items():
        st.session_state[f"sensor_{field}"] = value


def broker_error_message(error: Exception) -> str:
    return (
        f"Cannot reach LavinMQ at {redact_url(settings.url)} ({type(error).__name__}). "
        "Is the broker running, and is LAVINMQ_URL in .env correct?"
    )


@st.cache_resource
def get_consumer(url: str) -> BackgroundConsumer:
    consumer = BackgroundConsumer(manual_input_settings())
    consumer.start()
    return consumer


st.set_page_config(page_title="AURA Sensor Input", page_icon="🎛️", layout="wide")

load_env_file(PROJECT_ROOT / ".env")
try:
    settings = manual_input_settings()
except ValueError as error:
    st.error(f"Invalid LavinMQ settings: {error}")
    st.stop()
consumer = get_consumer(settings.url)

if "sensor_temperature_c" not in st.session_state:
    apply_preset("Normal")
    st.session_state.sensor_subject_id = DEFAULT_SUBJECT_ID
    st.session_state.sensor_history = []

# -- sidebar: broker and consumer -----------------------------------------------

with st.sidebar:
    st.header("Broker")
    st.code(redact_url(settings.url), language=None)
    st.caption(
        f"Exchange `{settings.exchange}` → queue `{settings.queue}`  \n"
        "Set `LAVINMQ_URL` in `.env` to use another broker, then restart."
    )
    st.header("Agents (consumer)")
    if consumer.running:
        st.success("Running: published readings are processed automatically.")
        if st.button("⏸ Stop consumer", use_container_width=True, help="Readings then wait in LavinMQ."):
            consumer.stop()
            st.rerun()
    else:
        st.warning("Stopped: published readings wait in the LavinMQ queue.")
        if st.button("▶ Start consumer", type="primary", use_container_width=True):
            consumer.start()
            st.rerun()
    if consumer.error:
        st.error(f"Consumer crashed: {consumer.error}")
    st.caption(
        "Don't also run `python -m aura.streaming.demo consume` against the same broker: "
        "two consumers on one queue split the messages between them."
    )

st.title("🎛️ Sensor input")
st.caption("Acts as the sensor device: enter a reading and publish it to LavinMQ. The agents' results appear below.")

# -- automatic test ---------------------------------------------------------------

with st.container(border=True):
    test_column, info_column = st.columns([1, 3])
    run_test = test_column.button("🧪 Run automatic test", type="primary", use_container_width=True)
    info_column.caption(
        "Publishes every preset through LavinMQ, waits for the agents, and checks each outcome. "
        "Also checks that an invalid 500 °C reading is blocked."
    )
    if run_test:
        with st.spinner("Publishing test readings and waiting for the agents…"):
            try:
                st.session_state.self_test = run_self_test(
                    settings, consumer, subject_id=DEFAULT_SUBJECT_ID
                )
            except (pika.exceptions.AMQPError, OSError) as error:
                st.session_state.self_test = None
                st.error(broker_error_message(error))
    test_results = st.session_state.get("self_test")
    if test_results:
        passed = sum(result.passed for result in test_results)
        summary = f"{passed}/{len(test_results)} checks passed"
        (st.success if passed == len(test_results) else st.error)(summary)
        st.dataframe(
            [
                {
                    "": "✅" if result.passed else "❌",
                    "check": result.name,
                    "expected": result.expected,
                    "actual": result.actual or "no result (timed out)",
                }
                for result in test_results
            ],
            hide_index=True,
            use_container_width=True,
        )
        if any(result.actual is None for result in test_results):
            st.caption(
                "No result usually means another consumer on the same broker took the message, "
                "or the broker is slow. Check the queue in the LavinMQ manager."
            )

# -- manual input -----------------------------------------------------------------

form_column, result_column = st.columns([2, 3])
with form_column:
    st.subheader("Manual reading")
    for name, column in zip(PRESETS, st.columns(2) * 2):
        column.button(name, on_click=apply_preset, args=(name,), use_container_width=True)

    st.text_input(
        "Resident ID",
        key="sensor_subject_id",
        help=f"The agents have a baseline for {DEFAULT_SUBJECT_ID}. "
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
            st.error(broker_error_message(error))
        else:
            st.success(f"Published {len(published)} event(s) to `{settings.exchange}`.")
            for event_id in published:
                st.session_state.sensor_history.insert(
                    0, {"sent": datetime.now().strftime("%H:%M:%S"), "event_id": event_id}
                )

    if events:
        with st.expander("Message that will be sent (JSON)"):
            st.json(json.loads(EventMessageCodec.encode(events[0])))


# -- live results -------------------------------------------------------------------

@st.fragment(run_every="2s")
def live_results() -> None:
    st.subheader("Processed by the agents")
    readings = consumer.results()
    if not readings:
        st.info(
            "Nothing processed yet. Publish a reading or run the automatic test."
            if consumer.running
            else "The consumer is stopped, so readings wait in LavinMQ. Start it in the sidebar."
        )
        return
    st.dataframe(
        [
            {
                "time": reading.received_at.astimezone().strftime("%H:%M:%S"),
                "resident": reading.subject_id,
                "temp °C": reading.observations.get("temperature_c"),
                "humidity %": reading.observations.get("humidity_pct"),
                "door": reading.observations.get("door_state"),
                "motion": reading.observations.get("motion_detected"),
                "outcome": OUTCOME_LABELS.get(reading.outcome, reading.outcome),
                "reasons": ", ".join(reading.reason_codes) or (reading.error or ""),
                "ticket": reading.ticket_id or "",
            }
            for reading in readings[:50]
        ],
        hide_index=True,
        use_container_width=True,
    )


with result_column:
    live_results()
