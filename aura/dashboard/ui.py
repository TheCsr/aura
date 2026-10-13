"""Streamlit multi-page dashboard UI for the AURA Multi-Agent Home Care System.

Visualizes the 4 agents working together, their task handoffs and drop decisions,
provides an interactive sensor event publisher/simulator, and manages action tickets.
"""

from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from uuid import uuid4

import streamlit as st

# Ensure repository root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env variables if not already present
def _load_dotenv_if_present() -> None:
    env_file = _PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key and key not in os.environ:
                            os.environ[key] = val
        except Exception as error:
            logging.warning(f"Could not load .env: {error}")

_load_dotenv_if_present()

from aura.contracts import InputEvent
from aura.dashboard.mock_adapter import DashboardRecord, MockDashboardAdapter
from aura.graph import WorkflowOutcome
from aura.messaging.adapters.lavinmq import (
    LavinMQSettings,
    PikaLavinMQConsumer,
    PikaLavinMQPublisher,
)
from aura.messaging.codec import EventMessageCodec
from aura.sensors.application import ValidatedSensorEventPublisher
from aura.sensors.validation import SensorEventValidator
from aura.streaming.composition import DEFAULT_BASELINE, DEFAULT_SUBJECT_ID, create_streaming_demo
from aura.streaming.demo import make_sensor_event, result_as_json
from aura.streaming.service import StreamingCareService, StreamingResult
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer
from aura.voice.session import ResidentVoiceSession
from aura.voice.testing import MockSpeaker


# ---------------------------------------------------------------------------
# State Initialization
# ---------------------------------------------------------------------------

def _get_or_create_state() -> None:
    if "aura_demo" not in st.session_state:
        st.session_state.aura_demo = create_streaming_demo()
    if "processed_results" not in st.session_state:
        st.session_state.processed_results: List[StreamingResult] = []
    if "latest_result" not in st.session_state:
        st.session_state.latest_result: Optional[StreamingResult] = None
    if "tickets_meta" not in st.session_state:
        # Map record_id -> {"status": ..., "notes": ..., "priority": ...}
        st.session_state.tickets_meta: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Processing Helpers
# ---------------------------------------------------------------------------

def _process_event_locally(event: InputEvent, resident_response: Optional[str] = None) -> StreamingResult:
    """Process an event through the 4-agent streaming pipeline in-memory."""
    demo = st.session_state.aura_demo
    if resident_response:
        # Recreate or update voice responder transcription text if requested
        demo.service._voice_responder._transcriber = FakeSpeechToTextAdapter(resident_response)

    result = demo.service.handle(event)
    st.session_state.latest_result = result

    # Keep list deduplicated by event_id
    existing_ids = [r.event_id for r in st.session_state.processed_results]
    if event.event_id not in existing_ids:
        st.session_state.processed_results.insert(0, result)

    # Initialize metadata for any new dashboard record (ticket)
    if result.receipt is not None and result.receipt.external_reference:
        rec_id = result.receipt.external_reference
        if rec_id not in st.session_state.tickets_meta:
            st.session_state.tickets_meta[rec_id] = {
                "status": "awaiting_human_review",
                "notes": "",
                "priority": "High" if "critical" in event.event_id else "Normal",
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
    return result


def _publish_to_lavinmq(event: InputEvent) -> tuple[bool, str]:
    """Publish an event to the configured LavinMQ broker."""
    try:
        settings = LavinMQSettings.from_environment()
        publisher = PikaLavinMQPublisher(settings)
        validated = ValidatedSensorEventPublisher(publisher)
        validated.publish(event)
        publisher.close()
        return True, f"Successfully published event {event.event_id} to exchange '{settings.exchange}' (routing key: '{settings.routing_key}')."
    except Exception as error:
        return False, f"Failed to publish to LavinMQ: {error}"


def _consume_one_from_lavinmq() -> tuple[Optional[StreamingResult], str]:
    """Fetch and process a single message from the LavinMQ queue if available."""
    try:
        import pika
        settings = LavinMQSettings.from_environment()
        params = pika.URLParameters(settings.url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        method, properties, body = channel.basic_get(queue=settings.queue, auto_ack=False)
        if method is None:
            connection.close()
            return None, "No messages waiting in queue."

        try:
            event = EventMessageCodec.decode(body)
            result = _process_event_locally(event)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            connection.close()
            return result, f"Consumed & processed event {event.event_id}."
        except Exception as proc_error:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            connection.close()
            return None, f"Rejected invalid message: {proc_error}"
    except Exception as error:
        return None, f"LavinMQ connection error: {error}"


# ---------------------------------------------------------------------------
# UI Views
# ---------------------------------------------------------------------------

def _render_agent_graph_view(result: Optional[StreamingResult]) -> None:
    st.subheader("🤖 Multi-Agent Workflow State & Task Handoff")

    if result is None:
        st.info("No sensor events have been processed yet. Use the **Quick Simulator** below or the **Sensor Simulator** page to trigger an event.")
        return

    # Outcome badge
    outcome_colors = {
        WorkflowOutcome.DISPATCHED: ("🟢 DISPATCHED (Ticket Created)", "#d4edda", "#155724"),
        WorkflowOutcome.NO_ACTION: ("⚪ NO ACTION (Task Dropped: Normal)", "#e2e3e5", "#383d41"),
        WorkflowOutcome.HELD: ("🟡 HELD (Task Dropped: Policy Threshold Not Met)", "#fff3cd", "#856404"),
        WorkflowOutcome.DENIED: ("🔴 DENIED (Task Dropped: Action Disallowed)", "#f8d7da", "#721c24"),
        WorkflowOutcome.DISPATCH_FAILED: ("⚠️ DISPATCH FAILED", "#f8d7da", "#721c24"),
    }
    badge_label, bg_color, text_color = outcome_colors.get(
        result.outcome, (str(result.outcome.value).upper(), "#e2e3e5", "#000000")
    )

    st.markdown(
        f"""
        <div style="background-color: {bg_color}; color: {text_color}; padding: 12px 18px; border-radius: 8px; margin-bottom: 20px; font-weight: 600; font-size: 16px; border: 1px solid rgba(0,0,0,0.1);">
            Event: <code>{result.event_id}</code> &nbsp;|&nbsp; Outcome: {badge_label}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 4 Agents visual progression
    col1, col2, col3, col4, col5 = st.columns(5)

    # 1. Supervisor
    with col1:
        st.markdown("### 1. Supervisor")
        if result.assessment.is_anomaly or result.assessment.requires_review:
            st.success("🚨 Anomaly Flagged")
            handoff_1 = "➡️ **Task Passed** to Voice Check"
        else:
            st.info("✅ Readings Normal")
            handoff_1 = "🛑 **Task Dropped** (No anomaly)"
        st.caption(f"Confidence: {result.assessment.confidence:.2f}")
        st.caption(handoff_1)

    # 2. Voice Check
    with col2:
        st.markdown("### 2. Voice Check")
        if result.voice_prompt is not None and result.voice_reply is not None:
            st.success("🎙️ Voice Analyzed")
            handoff_2 = "➡️ **Task Passed** to Triage"
        else:
            st.markdown("<span style='color:gray;'>⚪ Skipped / Not Run</span>", unsafe_allow_html=True)
            handoff_2 = "—"
        st.caption(handoff_2)

    # 3. Triage
    with col3:
        st.markdown("### 3. Triage")
        if result.proposal is not None:
            st.success("📋 Action Proposed")
            st.caption(f"Type: `{result.proposal.action_type}`")
            handoff_3 = "➡️ **Task Passed** to Guardian"
        else:
            st.markdown("<span style='color:gray;'>⚪ Skipped / Not Run</span>", unsafe_allow_html=True)
            handoff_3 = "—"
        st.caption(handoff_3)

    # 4. Guardian
    with col4:
        st.markdown("### 4. Guardian")
        if result.decision is not None:
            if result.decision.status.value == "approve":
                st.success("🛡️ Approved")
                handoff_4 = "➡️ **Task Passed** to Dispatcher"
            elif result.decision.status.value == "hold":
                st.warning("⏸️ Held")
                handoff_4 = "🛑 **Task Dropped** (Policy block)"
            else:
                st.error("⛔ Denied")
                handoff_4 = "🛑 **Task Dropped** (Denied)"
        else:
            st.markdown("<span style='color:gray;'>⚪ Skipped / Not Run</span>", unsafe_allow_html=True)
            handoff_4 = "—"
        st.caption(handoff_4)

    # 5. Dispatcher / Ticket
    with col5:
        st.markdown("### 5. Dispatcher")
        if result.receipt is not None:
            st.success("🎫 Ticket Created")
            st.caption(f"Ref: `{result.receipt.external_reference}`")
        else:
            st.markdown("<span style='color:gray;'>⚪ Skipped / No Ticket</span>", unsafe_allow_html=True)
            st.caption("No action executed")

    st.markdown("---")

    # Step-by-step trace expansion
    with st.expander("🔍 Deep Inspection: Agent Task Handoff Trace", expanded=True):
        tabs = st.tabs(["Supervisor Assessment", "Resident Voice Session", "Triage Proposal", "Guardian Decision", "Dispatcher Receipt"])

        with tabs[0]:
            st.markdown("**Supervisor Agent** compares sensor observations against resident baseline tolerances.")
            st.write({
                "is_anomaly": result.assessment.is_anomaly,
                "requires_review": result.assessment.requires_review,
                "summary": result.assessment.summary,
                "confidence": result.assessment.confidence,
                "evidence": [
                    {"source": e.source, "description": e.description, "details": dict(e.details)}
                    for e in result.assessment.evidence
                ],
            })

        with tabs[1]:
            if result.voice_prompt is not None and result.voice_reply is not None:
                st.markdown("**Prompt Delivered to Resident:**")
                st.info(f"🗣️ \"{result.voice_prompt.text}\"")
                st.markdown("**Resident Voice Reply & Speech-to-Text:**")
                st.write(f"📝 Transcript: **\"{result.voice_reply.transcript.text}\"** (Source: `{result.voice_reply.transcript.source}`)")
                st.markdown("**Acoustic Signal Features (PCM WAV Analyzer):**")
                st.json(result.voice_reply.signal_analysis.features)
            else:
                st.write("Voice interaction was not initiated because Supervisor determined no anomaly occurred.")

        with tabs[2]:
            if result.proposal is not None:
                st.write({
                    "proposal_id": result.proposal.proposal_id,
                    "action_type": result.proposal.action_type,
                    "rationale": result.proposal.rationale,
                    "confidence": result.proposal.confidence,
                    "parameters": dict(result.proposal.parameters),
                    "evidence_refs": list(result.proposal.evidence_refs),
                })
            else:
                st.write("Triage was not invoked because the event was dropped before triage.")

        with tabs[3]:
            if result.decision is not None:
                st.write({
                    "status": result.decision.status.value,
                    "reason_codes": list(result.decision.reason_codes),
                    "rationale": result.decision.rationale,
                })
            else:
                st.write("Guardian was not invoked.")

        with tabs[4]:
            if result.receipt is not None:
                st.write({
                    "status": result.receipt.status.value,
                    "message": result.receipt.message,
                    "external_reference": result.receipt.external_reference,
                })
                st.success(f"Ticket successfully generated and dispatched with ID: `{result.receipt.external_reference}`")
            else:
                st.write("No ticket or action dispatched.")


def _render_tickets_view() -> None:
    st.subheader("🎫 Caregiver Review Tickets")
    st.caption("Tickets created when Guardian approves a human-review dispatch action.")

    demo = st.session_state.aura_demo
    records = demo.dashboard.records

    if not records:
        st.info("No tickets have been created yet. When a critical anomaly occurs and Guardian approves human review, a ticket is automatically dispatched here.")
        return

    # Metrics
    meta_store = st.session_state.tickets_meta
    awaiting_count = sum(1 for r in records if meta_store.get(r.record_id, {}).get("status") == "awaiting_human_review")
    in_prog_count = sum(1 for r in records if meta_store.get(r.record_id, {}).get("status") == "in_progress")
    resolved_count = sum(1 for r in records if meta_store.get(r.record_id, {}).get("status") == "resolved")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Tickets", len(records))
    m2.metric("Awaiting Review", awaiting_count)
    m3.metric("In Progress", in_prog_count)
    m4.metric("Resolved", resolved_count)

    st.markdown("---")

    filter_status = st.selectbox(
        "Filter by status",
        ["All", "awaiting_human_review", "in_progress", "resolved"],
        index=0,
    )

    for record in reversed(records):
        meta = meta_store.setdefault(
            record.record_id,
            {
                "status": "awaiting_human_review",
                "notes": "",
                "priority": "High" if "critical" in record.event_id else "Normal",
                "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            },
        )

        if filter_status != "All" and meta["status"] != filter_status:
            continue

        status_colors = {
            "awaiting_human_review": "🔴 Awaiting Review",
            "in_progress": "🟡 In Progress",
            "resolved": "🟢 Resolved",
        }

        with st.expander(f"Ticket `{record.record_id}` — {status_colors.get(meta['status'], meta['status'])} — Resident: {record.subject_id}", expanded=(meta["status"] == "awaiting_human_review")):
            c1, c2 = st.columns([2, 1])

            with c1:
                st.markdown(f"**Rationale / Summary:** {record.summary}")
                st.caption(f"Event ID: `{record.event_id}` · Proposal ID: `{record.proposal_id}`")
                st.caption(f"Created: {meta.get('created_at', record.created_at.isoformat())}")

                st.markdown("**Sensor Observations:**")
                obs = dict(record.observations)
                vocal_features = obs.pop("vocal_signal_features", None)
                st.json(obs)

                if vocal_features:
                    st.markdown("**Voice Signal Acoustic Features:**")
                    st.json(vocal_features)

            with c2:
                st.markdown("#### Caregiver Action")
                current_status = meta["status"]
                new_status = st.selectbox(
                    "Update Status",
                    ["awaiting_human_review", "in_progress", "resolved"],
                    index=["awaiting_human_review", "in_progress", "resolved"].index(current_status),
                    key=f"status_select_{record.record_id}",
                )
                if new_status != current_status:
                    meta["status"] = new_status
                    st.rerun()

                notes = st.text_area(
                    "Caregiver Notes",
                    value=meta.get("notes", ""),
                    key=f"notes_{record.record_id}",
                    placeholder="Enter observation notes or action taken...",
                )
                if st.button("Save Notes", key=f"save_btn_{record.record_id}"):
                    meta["notes"] = notes
                    st.success("Notes saved.")


def _render_simulator_view() -> None:
    st.subheader("📡 Sensor Event Simulator & Publisher")
    st.caption("Generate synthetic sensor readings, test the multi-agent pipeline offline, or publish to LavinMQ.")

    sim_tab1, sim_tab2 = st.tabs(["Preset Scenarios", "Custom Sensor Observations"])

    with sim_tab1:
        st.markdown("Select a standardized scenario to test how agents evaluate risk and dispatch:")
        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("#### ❄️ Critical Cold")
            st.caption("Temp: -20°C, Door: open, Motion: false")
            st.caption("Expected: Supervisor flags anomaly → Voice Check → Guardian APPROVES → **Ticket Created**")
            if st.button("Run Critical Cold", type="primary", use_container_width=True):
                evt = make_sensor_event("critical-cold")
                res = _process_event_locally(evt)
                st.success(f"Processed: Outcome = {res.outcome.value}")
                st.rerun()

        with c2:
            st.markdown("#### 🚪 Door Open Anomaly")
            st.caption("Temp: 18°C, Door: open, Motion: false")
            st.caption("Expected: Supervisor flags anomaly → Voice Check → Guardian evaluates → **Check Policy**")
            if st.button("Run Door Open", use_container_width=True):
                evt = make_sensor_event("door-open")
                res = _process_event_locally(evt)
                st.success(f"Processed: Outcome = {res.outcome.value}")
                st.rerun()

        with c3:
            st.markdown("#### 🌿 Normal Baseline")
            st.caption("Temp: 18°C, Door: closed, Motion: true")
            st.caption("Expected: Supervisor compares with baseline → **No Anomaly (Task Dropped)**")
            if st.button("Run Normal Event", use_container_width=True):
                evt = make_sensor_event("normal")
                res = _process_event_locally(evt)
                st.success(f"Processed: Outcome = {res.outcome.value}")
                st.rerun()

    with sim_tab2:
        st.markdown("Configure specific sensor values:")
        col_a, col_b = st.columns(2)
        with col_a:
            temp = st.slider("Temperature (°C)", min_value=-30.0, max_value=50.0, value=18.0, step=0.5)
            humidity = st.slider("Humidity (%)", min_value=0.0, max_value=100.0, value=45.0, step=1.0)
        with col_b:
            door = st.selectbox("Door State", ["closed", "open"])
            motion = st.selectbox("Motion Detected", [True, False], format_func=lambda v: "Yes (True)" if v else "No (False)")

        resident_reply = st.text_input(
            "Simulated Resident Voice Response",
            value="Jag mår bra, oroa dig inte.",
            help="Text transcribed by the speech-to-text adapter during resident voice check.",
        )

        target_mode = st.radio(
            "Execution Target",
            ["Direct In-Memory Workflow (Instant)", "Publish via LavinMQ AMQP Broker"],
            horizontal=True,
        )

        if st.button("🚀 Execute Sensor Event", type="primary"):
            custom_event = InputEvent(
                event_id=f"sensor-custom-{uuid4().hex[:8]}",
                occurred_at=datetime.now(timezone.utc),
                subject_id=DEFAULT_SUBJECT_ID,
                source="dashboard-ui-simulator",
                observations={
                    "temperature_c": float(temp),
                    "humidity_pct": float(humidity),
                    "door_state": door,
                    "motion_detected": motion,
                },
            )

            if target_mode.startswith("Direct"):
                result = _process_event_locally(custom_event, resident_response=resident_reply)
                st.success(f"Workflow Complete! Outcome: **{result.outcome.value}**")
                st.rerun()
            else:
                ok, msg = _publish_to_lavinmq(custom_event)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.markdown("---")
    st.subheader("📬 LavinMQ Broker Consumer Control")
    st.caption("Pull waiting messages from the CloudAMQP broker directly into the dashboard.")
    c_btn1, c_info = st.columns([1, 2])
    with c_btn1:
        if st.button("📥 Consume 1 Message from Queue"):
            res, msg = _consume_one_from_lavinmq()
            if res is not None:
                st.success(f"{msg} Outcome: {res.outcome.value}")
                st.rerun()
            else:
                st.info(msg)


def _render_history_view() -> None:
    st.subheader("📋 Processed Event History")
    results = st.session_state.processed_results

    if not results:
        st.info("No events have been processed yet.")
        return

    st.caption(f"Showing {len(results)} processed events in this session.")

    for item in results:
        with st.expander(f"Event: `{item.event_id}` — Outcome: **{item.outcome.value}**", expanded=False):
            st.json(json.loads(result_as_json(item)))


# ---------------------------------------------------------------------------
# Main App Structure with Sidebar Navigation
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="AURA - Home Care Multi-Agent System",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _get_or_create_state()

    st.sidebar.title("🛡️ AURA System")
    st.sidebar.caption("Multi-Agent Swedish Home Care")

    menu = st.sidebar.radio(
        "Navigation",
        [
            "📊 Dashboard",
            "🎫 Tickets",
            "📡 Sensor Simulator",
            "📋 Event History",
        ],
        index=0,
    )

    # Sidebar Quick Info
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚙️ System Status")
    broker_configured = bool(os.environ.get("LAVINMQ_URL"))
    st.sidebar.markdown(f"**LavinMQ Broker:** {'🟢 Configured' if broker_configured else '⚪ Offline / Direct'}")
    total_tickets = len(st.session_state.aura_demo.dashboard.records)
    st.sidebar.markdown(f"**Open Tickets:** {total_tickets}")
    st.sidebar.markdown(f"**Events Processed:** {len(st.session_state.processed_results)}")

    if menu == "📊 Dashboard":
        st.title("📊 Multi-Agent Operations Dashboard")
        st.caption("Real-time orchestration: Supervisor ➔ Voice Check ➔ Triage ➔ Guardian ➔ Dispatcher")
        _render_agent_graph_view(st.session_state.latest_result)

        st.markdown("---")
        st.subheader("⚡ Quick Simulator")
        q1, q2, q3 = st.columns(3)
        with q1:
            if st.button("Trigger Critical Cold (Dispatched)", key="quick_cold"):
                evt = make_sensor_event("critical-cold")
                _process_event_locally(evt)
                st.rerun()
        with q2:
            if st.button("Trigger Door Open (Held)", key="quick_door"):
                evt = make_sensor_event("door-open")
                _process_event_locally(evt)
                st.rerun()
        with q3:
            if st.button("Trigger Normal (No Action)", key="quick_normal"):
                evt = make_sensor_event("normal")
                _process_event_locally(evt)
                st.rerun()

    elif menu == "🎫 Tickets":
        st.title("🎫 Incident & Review Tickets")
        _render_tickets_view()

    elif menu == "📡 Sensor Simulator":
        st.title("📡 Sensor Event Simulator")
        _render_simulator_view()

    elif menu == "📋 Event History":
        st.title("📋 Processed Events")
        _render_history_view()


if __name__ == "__main__":
    main()
