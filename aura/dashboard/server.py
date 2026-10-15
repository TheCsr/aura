"""FastAPI backend server for the AURA React Dashboard."""

import base64
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from math import isfinite
import os
from pathlib import Path
from statistics import median
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure project root in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load environment variables from .env if present
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
        except Exception as error:
            logging.warning("Error loading .env: %s", error)

_load_env()

from aura.contracts import InputEvent
from aura.graph import WorkflowOutcome
from aura.messaging.adapters.lavinmq import (
    LavinMQSettings,
    PikaLavinMQConsumer,
    PikaLavinMQPublisher,
)
from aura.messaging.codec import EventMessageCodec
from aura.sensors.application import ValidatedSensorEventPublisher
from aura.streaming.composition import DEFAULT_SUBJECT_ID, create_streaming_demo
from aura.streaming.demo import make_sensor_event, result_as_json
from aura.streaming.service import StreamingResult
from aura.voice.adapters.google_tts import GoogleTranslateTtsAdapter
from aura.voice.adapters.orange_kb_whisper import OrangeKbWhisperAdapter
from aura.voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer
from aura.voice.contracts import AudioRecording

logger = logging.getLogger("aura.dashboard.server")


@asynccontextmanager
async def _dashboard_lifespan(_app: FastAPI):
    _start_broker_consumer()
    _start_expiry_sweeper()
    try:
        yield
    finally:
        _stop_expiry_sweeper()
        _stop_broker_consumer()


app = FastAPI(
    title="AURA Multi-Agent Home Care API",
    version="1.0.0",
    lifespan=_dashboard_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared In-Memory State
_state_lock = Lock()
_demo = create_streaming_demo()
_processed_results: List[StreamingResult] = []
_latest_result: Optional[StreamingResult] = None
_tickets_meta: Dict[str, Dict[str, Any]] = {}
_event_sensor_data: Dict[str, Dict[str, Any]] = {}
_ticket_feedback: Dict[str, Dict[str, Any]] = {}
_baseline_suggestions: Dict[Tuple[str, str], Dict[str, Any]] = {}
_buddy_requests: List[Dict[str, Any]] = []
_FALSE_ALARM_SUGGESTION_THRESHOLD = 3
_SYNTHETIC_BUDDIES = (
    {
        "buddy_id": "demo-companion-anna",
        "name": "Demo Companion Anna",
        "relationship": "Daughter",
        "nda_signed": True,
        "description": "A friendly conversation about everyday life, hobbies, or how your day is going.",
        "language": "Swedish",
        "synthetic": True,
    },
    {
        "buddy_id": "demo-companion-erik",
        "name": "Demo Companion Erik",
        "relationship": "Son",
        "nda_signed": True,
        "description": "A calm conversation partner for a friendly chat and company.",
        "language": "Swedish",
        "synthetic": True,
    },
)

_google_tts = GoogleTranslateTtsAdapter(default_language="sv")
_kb_whisper = OrangeKbWhisperAdapter()
_signal_analyzer = PcmWavSignalAnalyzer()
_broker_consumer: Optional[PikaLavinMQConsumer] = None
_broker_consumer_thread: Optional[Thread] = None
_expiry_sweeper_stop = Event()
_expiry_sweeper_thread: Optional[Thread] = None


def _handle_broker_event(event: InputEvent) -> StreamingResult:
    with _state_lock:
        result = _demo.service.start(event)
        _record_result(result, event)
        return result


def _start_broker_consumer() -> None:
    global _broker_consumer, _broker_consumer_thread
    if _broker_consumer_thread is not None and _broker_consumer_thread.is_alive():
        return
    try:
        settings = LavinMQSettings.from_environment()
    except ValueError:
        logger.info("LavinMQ is not configured; dashboard broker consumer is disabled")
        return

    _broker_consumer = PikaLavinMQConsumer(
        settings,
        _handle_broker_event,
    )
    _broker_consumer_thread = Thread(
        target=_broker_consumer.run_forever,
        name="aura-lavinmq-consumer",
        daemon=True,
    )
    _broker_consumer_thread.start()
    logger.info("Started dashboard LavinMQ consumer")


def _stop_broker_consumer() -> None:
    global _broker_consumer, _broker_consumer_thread
    if _broker_consumer is not None:
        _broker_consumer.stop()
    if _broker_consumer_thread is not None:
        _broker_consumer_thread.join(timeout=5.0)
    _broker_consumer = None
    _broker_consumer_thread = None


def _sweep_expired_conversations() -> None:
    with _state_lock:
        for result in _demo.service.sweep_expired_conversations():
            _record_result(result)


def _run_expiry_sweeper() -> None:
    while not _expiry_sweeper_stop.wait(1.0):
        try:
            _sweep_expired_conversations()
        except Exception:
            logger.error("Pending-conversation escalation sweep failed")


def _start_expiry_sweeper() -> None:
    global _expiry_sweeper_thread
    if _expiry_sweeper_thread is not None and _expiry_sweeper_thread.is_alive():
        return
    _expiry_sweeper_stop.clear()
    _expiry_sweeper_thread = Thread(
        target=_run_expiry_sweeper,
        name="aura-reply-deadline-sweeper",
        daemon=True,
    )
    _expiry_sweeper_thread.start()


def _stop_expiry_sweeper() -> None:
    global _expiry_sweeper_thread
    _expiry_sweeper_stop.set()
    if _expiry_sweeper_thread is not None:
        _expiry_sweeper_thread.join(timeout=2.0)
    _expiry_sweeper_thread = None


def _serialize_result(result: StreamingResult) -> Dict[str, Any]:
    payload = json.loads(result_as_json(result))
    sensor_data = _event_sensor_data.get(result.event_id)
    if sensor_data is not None:
        payload["sensor_data"] = sensor_data
    return payload


def _record_result(result: StreamingResult, event: Optional[InputEvent] = None) -> None:
    global _latest_result
    _latest_result = result
    if event is not None:
        _event_sensor_data[result.event_id] = {
            "observations": dict(event.observations),
            "source": event.source,
            "subject_id": event.subject_id,
            "occurred_at": event.occurred_at.isoformat(),
            "is_mock": event.source.startswith("synthetic-") or "simulator" in event.source,
        }
    for index, previous in enumerate(_processed_results):
        if previous.event_id == result.event_id:
            _processed_results[index] = result
            break
    else:
        _processed_results.insert(0, result)

    is_ticket_action = (
        result.proposal is not None
        and result.proposal.action_type in _demo.dashboard.TICKET_ACTIONS
    )
    if is_ticket_action and result.receipt is not None and result.receipt.external_reference:
        rec_id = result.receipt.external_reference
        if rec_id not in _tickets_meta:
            record = next(
                (item for item in _demo.dashboard.records if item.record_id == rec_id),
                None,
            )
            _tickets_meta[rec_id] = {
                "status": record.status if record is not None else "awaiting_human_review",
                "notes": "",
                "priority": record.priority if record is not None else "Normal",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }


class SimulateRequest(BaseModel):
    scenario: Optional[str] = "critical-cold"
    custom_observations: Optional[Dict[str, Any]] = None


class PublishRequest(BaseModel):
    scenario: Optional[str] = "critical-cold"
    custom_observations: Optional[Dict[str, Any]] = None


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    priority: Optional[str] = None


class TicketFeedbackRequest(BaseModel):
    label: Literal["false_alarm", "justified", "sensor_fault"]
    note: str = ""


class BuddyRequestBody(BaseModel):
    subject_id: str


@app.get("/api/status")
def get_status():
    with _state_lock:
        broker_url = os.environ.get("LAVINMQ_URL", "")
        broker_configured = bool(broker_url.strip())
        records = _demo.dashboard.records
        awaiting_count = sum(
            1 for r in records if _tickets_meta.get(r.record_id, {}).get("status") == "awaiting_human_review"
        )
        in_progress_count = sum(
            1 for r in records if _tickets_meta.get(r.record_id, {}).get("status") == "in_progress"
        )
        resolved_count = sum(
            1 for r in records if _tickets_meta.get(r.record_id, {}).get("status") == "resolved"
        )
        return {
            "broker_configured": broker_configured,
            "broker_host": broker_url.split("@")[-1].split("/")[0] if "@" in broker_url else None,
            "total_events_processed": len(_processed_results),
            "total_tickets": len(records),
            "awaiting_review": awaiting_count,
            "in_progress": in_progress_count,
            "resolved": resolved_count,
        }


@app.get("/api/metrics")
def get_metrics():
    with _state_lock:
        results = tuple(_processed_results)
        records = _demo.dashboard.records
        feedback_values = tuple(_ticket_feedback.values())
        feedback_total = len(feedback_values)
        false_alarm_total = sum(item["label"] == "false_alarm" for item in feedback_values)
        dismissed_critical = sum(
            result.outcome == WorkflowOutcome.DISMISSED
            and result.assessment.severity is not None
            and result.assessment.severity.value == "critical"
            for result in results
        )
        visit_actions = {"home_visit", "urgent_visit"}
        pending_outcomes = {
            WorkflowOutcome.RUNNING,
            WorkflowOutcome.AWAITING_RESIDENT,
            WorkflowOutcome.WAITING_FOR_RESIDENT,
        }
        estimated_visits_avoided = sum(
            result.assessment.is_anomaly
            and result.outcome not in pending_outcomes
            and (
                result.proposal is None
                or result.proposal.action_type not in visit_actions
            )
            for result in results
        )
        return {
            "events_processed": len(results),
            "dropped": sum(result.outcome == WorkflowOutcome.DROPPED for result in results),
            "no_action": sum(result.outcome == WorkflowOutcome.NO_ACTION for result in results),
            "dismissed": sum(result.outcome == WorkflowOutcome.DISMISSED for result in results),
            "dismissed_critical_review": dismissed_critical,
            "remote_followups": sum(record.action_type == "remote_followup" for record in records),
            "home_visits": sum(record.action_type == "home_visit" for record in records),
            "urgent_visits": sum(record.action_type == "urgent_visit" for record in records),
            "estimated_visits_avoided": estimated_visits_avoided,
            "escalated_no_reply": sum(result.outcome == WorkflowOutcome.ESCALATED_NO_REPLY for result in results),
            "sensor_faults": sum(result.outcome == WorkflowOutcome.SENSOR_FAULT for result in results),
            "false_alarm_feedback": false_alarm_total,
            "feedback_total": feedback_total,
            "false_alarm_rate": false_alarm_total / feedback_total if feedback_total else 0.0,
        }


@app.get("/api/latest")
def get_latest():
    with _state_lock:
        if _latest_result is None:
            return {"latest": None}
        _refresh_conversation_results()
        return {"latest": _serialize_result(_latest_result)}


@app.get("/api/events")
def get_events(limit: int = 50):
    with _state_lock:
        _refresh_conversation_results()
        items = [_serialize_result(r) for r in _processed_results[:limit]]
        return {"events": items}


@app.post("/api/simulate")
def simulate_event(req: SimulateRequest):
    with _state_lock:
        if req.custom_observations:
            event = InputEvent(
                event_id=f"sensor-custom-{uuid4().hex[:8]}",
                occurred_at=datetime.now(timezone.utc),
                subject_id=DEFAULT_SUBJECT_ID,
                source="react-dashboard-simulator",
                observations=req.custom_observations,
            )
        else:
            event = make_sensor_event(req.scenario or "critical-cold")

        result = _demo.service.start(event)
        _record_result(result, event)
        return {
            "success": True,
            "result": _serialize_result(result),
            "tickets_count": len(_demo.dashboard.records),
        }


@app.post("/api/publish")
def publish_event(req: PublishRequest):
    try:
        settings = LavinMQSettings.from_environment()
        if req.custom_observations:
            event = InputEvent(
                event_id=f"sensor-custom-{uuid4().hex[:8]}",
                occurred_at=datetime.now(timezone.utc),
                subject_id=DEFAULT_SUBJECT_ID,
                source="react-dashboard-publisher",
                observations=req.custom_observations,
            )
        else:
            event = make_sensor_event(req.scenario or "critical-cold")

        publisher = PikaLavinMQPublisher(settings)
        validated_pub = ValidatedSensorEventPublisher(publisher)
        validated_pub.publish(event)
        publisher.close()

        return {
            "success": True,
            "published_event_id": event.event_id,
            "exchange": settings.exchange,
            "routing_key": settings.routing_key,
        }
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LavinMQ Publish Error: {error}",
        )


@app.post("/api/consume")
def consume_event():
    import pika

    try:
        settings = LavinMQSettings.from_environment()
        params = pika.URLParameters(settings.url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        method, properties, body = channel.basic_get(queue=settings.queue, auto_ack=False)
        if method is None:
            connection.close()
            return {"success": False, "message": "No messages pending in LavinMQ queue."}

        with _state_lock:
            try:
                event = EventMessageCodec.decode(body)
                result = _demo.service.start(event)
                _record_result(result, event)
                channel.basic_ack(delivery_tag=method.delivery_tag)
                connection.close()
                return {
                    "success": True,
                    "consumed_event_id": event.event_id,
                    "result": _serialize_result(result),
                }
            except Exception as proc_error:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                connection.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Message decoding or validation failed: {proc_error}",
                )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LavinMQ connection error: {error}",
        )


# ---------------------------------------------------------------------------
# Resident Home Device Endpoints
# ---------------------------------------------------------------------------

class DeviceTriggerRequest(BaseModel):
    scenario: Optional[str] = "critical-cold"
    custom_observations: Optional[Dict[str, Any]] = None


def _refresh_conversation_results() -> None:
    global _latest_result
    results = {result.event_id: result for result in _demo.service.results()}
    for index, previous in enumerate(_processed_results):
        if previous.event_id in results:
            _processed_results[index] = results[previous.event_id]
    if _latest_result is not None:
        _latest_result = results.get(_latest_result.event_id, _latest_result)


def _device_prompt(result: StreamingResult) -> Optional[Dict[str, Any]]:
    prompt = result.voice_prompt
    if prompt is None or result.outcome not in {
        WorkflowOutcome.AWAITING_RESIDENT,
        WorkflowOutcome.WAITING_FOR_RESIDENT,
    }:
        return None
    sensor_data = _event_sensor_data[result.event_id]
    return {
        "event_id": prompt.event_id,
        "conversation_id": prompt.conversation_id,
        "subject_id": sensor_data["subject_id"],
        "text": prompt.text,
        "created_at": sensor_data["occurred_at"],
        "status": "pending_reply",
        "observations": sensor_data["observations"],
        "reply_timeout_seconds": _demo.service.reply_timeout_seconds(prompt.conversation_id),
        "remaining_reply_seconds": _demo.service.remaining_reply_seconds(prompt.conversation_id),
        "playback": {
            "output_mode": prompt.speaker_receipt.output_mode,
            "delivered": prompt.speaker_receipt.delivered,
        },
    }


class PlaybackRequest(BaseModel):
    event_id: str
    subject_id: str
    conversation_id: str
    provider: Literal["browser_speech", "gtts"]
    status: Literal["started", "completed", "stopped", "failed", "blocked"]


@app.post("/api/device/playback")
def record_device_playback(receipt: PlaybackRequest):
    with _state_lock:
        try:
            result = _demo.service.record_playback(**receipt.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        _record_result(result)
        return {"success": True, "playback": _serialize_result(result)["voice_prompt"]}


@app.get("/api/device/prompt")
def get_device_prompt(subject_id: str = DEFAULT_SUBJECT_ID):
    with _state_lock:
        prompts = _demo.service.pending_prompts(subject_id)
        _refresh_conversation_results()
        result = next((item for item in _processed_results if prompts and item.event_id == prompts[0].event_id), None)
        return {
            "prompt": _device_prompt(result) if result is not None else None,
            "whisper_configured": _kb_whisper.is_configured,
        }


@app.post("/api/device/trigger-prompt")
def trigger_device_prompt(req: DeviceTriggerRequest):
    with _state_lock:
        if req.custom_observations:
            event = InputEvent(
                event_id=f"sensor-device-{uuid4().hex[:8]}",
                occurred_at=datetime.now(timezone.utc),
                subject_id=DEFAULT_SUBJECT_ID,
                source="synthetic-device-trigger",
                observations=req.custom_observations,
            )
        else:
            event = make_sensor_event(req.scenario or "critical-cold", event_id=f"sensor-device-{uuid4().hex[:8]}")

        result = _demo.service.start(event)
        _record_result(result, event)
        if result.outcome == WorkflowOutcome.NO_ACTION:
            return {
                "success": False,
                "message": "Supervisor evaluated readings as normal. No resident prompt required.",
                "outcome": "no_action",
            }

        return {
            "success": True,
            "prompt": _device_prompt(result),
            "whisper_configured": _kb_whisper.is_configured,
        }


@app.get("/api/device/tts")
def get_device_tts(text: str):
    if not text.strip() or len(text) > 2000:
        raise HTTPException(status_code=422, detail="Prompt text must contain 1 to 2000 characters")
    try:
        audio_bytes = _google_tts.synthesize(text, language="sv")
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"},
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Speech playback could not be generated; please retry") from None


@app.post("/api/device/respond")
def respond_from_device(
    event_id: str = Form(...),
    subject_id: str = Form(...),
    conversation_id: str = Form(...),
    audio_file: UploadFile = File(...),
):
    audio_bytes = audio_file.file.read(5 * 1024 * 1024 + 1)
    if len(audio_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Recording exceeds the 5 MiB limit")
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Recording is empty")
    if not _kb_whisper.is_configured:
        raise HTTPException(status_code=503, detail="KB-Whisper is not configured on the backend")
    recording = AudioRecording(audio_bytes, audio_file.filename or "reply.wav", "audio/wav")
    with _state_lock:
        try:
            result = _demo.service.resume_audio(
                event_id=event_id, subject_id=subject_id, conversation_id=conversation_id,
                recording=recording, transcriber=_kb_whisper, signal_analyzer=_signal_analyzer,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except Exception:
            raise HTTPException(status_code=502, detail="Voice or agent processing failed; the conversation is pending for retry") from None
        _record_result(result)
        has_ticket = (
            result.receipt is not None
            and result.proposal is not None
            and result.proposal.action_type in _demo.dashboard.TICKET_ACTIONS
        )
        ticket_id = result.receipt.external_reference if has_ticket else None
        action_record_id = (
            result.receipt.external_reference
            if result.receipt is not None and not has_ticket
            else None
        )

        return {
            "success": True,
            "event_id": result.event_id,
            "conversation_id": result.voice_prompt.conversation_id,
            "transcript": result.voice_reply.transcript.text,
            "stt_source": result.voice_reply.transcript.source,
            "outcome": result.outcome.value,
            "ticket_id": ticket_id,
            "action_record_id": action_record_id,
            "guardian_status": result.decision.status.value if result.decision else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/api/tickets")
def get_tickets():
    with _state_lock:
        records = _demo.dashboard.records
        tickets_list = []
        for r in reversed(records):
            meta = _tickets_meta.get(r.record_id, {
                "status": r.status,
                "notes": "",
                "priority": r.priority,
                "updated_at": r.created_at.isoformat(),
            })
            tickets_list.append({
                "record_id": r.record_id,
                "event_id": r.event_id,
                "proposal_id": r.proposal_id,
                "action_type": r.action_type,
                "triage_stage": r.triage_stage,
                "subject_id": r.subject_id,
                "created_at": r.created_at.isoformat(),
                "status": meta.get("status", r.status),
                "notes": meta.get("notes", ""),
                "priority": meta.get("priority", r.priority),
                "summary": r.summary,
                "observations": dict(r.observations),
                "feedback": _ticket_feedback.get(r.record_id),
            })
        return {"tickets": tickets_list}


@app.get("/api/action-records")
def get_action_records():
    with _state_lock:
        return {
            "records": [
                {
                    "record_id": record.record_id,
                    "event_id": record.event_id,
                    "proposal_id": record.proposal_id,
                    "action_type": record.action_type,
                    "record_type": record.record_type,
                    "subject_id": record.subject_id,
                    "created_at": record.created_at.isoformat(),
                    "reason": record.reason,
                    "parameters": dict(record.parameters),
                }
                for record in _demo.dashboard.action_records
            ]
        }


@app.get("/api/buddies")
def get_demo_buddies():
    return {"buddies": list(_SYNTHETIC_BUDDIES), "demo_only": True}


@app.get("/api/buddy-requests")
def get_buddy_requests():
    with _state_lock:
        return {"requests": list(_buddy_requests), "demo_only": True}


@app.post("/api/buddies/{buddy_id}/request")
def request_demo_buddy(buddy_id: str, request: BuddyRequestBody):
    buddy = next((item for item in _SYNTHETIC_BUDDIES if item["buddy_id"] == buddy_id), None)
    if buddy is None:
        raise HTTPException(status_code=404, detail="Demo buddy not found")
    if not request.subject_id.strip():
        raise HTTPException(status_code=422, detail="subject_id must not be empty")
    with _state_lock:
        entry = {
            "request_id": str(uuid4()),
            "buddy_id": buddy_id,
            "buddy_name": buddy["name"],
            "subject_id": request.subject_id.strip(),
            "status": "queued_demo",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _buddy_requests.append(entry)
        return {"success": True, "request": entry, "demo_only": True}


def _baseline_value_changed(observed: Any, baseline: Any) -> bool:
    if isinstance(observed, bool) or isinstance(baseline, bool):
        return type(observed) is not type(baseline) or observed != baseline
    if isinstance(observed, (int, float)) and isinstance(baseline, (int, float)):
        return isfinite(observed) and isfinite(baseline) and observed != baseline
    return type(observed) is not type(baseline) or observed != baseline


def _refresh_baseline_suggestions(subject_id: str) -> None:
    baseline = _demo.baseline_store.get_baseline(subject_id)
    if baseline is None:
        return
    matching_records = {record.record_id: record for record in _demo.dashboard.records}
    samples_by_field: Dict[str, List[Any]] = {}
    for ticket_id, feedback in _ticket_feedback.items():
        record = matching_records.get(ticket_id)
        if (
            record is None
            or record.subject_id != subject_id
            or feedback["label"] != "false_alarm"
        ):
            continue
        for field, observed in record.observations.items():
            if field in baseline and _baseline_value_changed(observed, baseline[field]):
                if observed is None or isinstance(observed, (str, int, float, bool)):
                    samples_by_field.setdefault(field, []).append(observed)

    for field, values in samples_by_field.items():
        if len(values) < _FALSE_ALARM_SUGGESTION_THRESHOLD:
            continue
        key = (subject_id, field)
        existing = _baseline_suggestions.get(key)
        if existing is not None and existing["status"] == "applied":
            continue
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            suggested_value = median(values)
        else:
            suggested_value = Counter(values).most_common(1)[0][0]
        _baseline_suggestions[key] = {
            "subject_id": subject_id,
            "field": field,
            "current_value": baseline[field],
            "suggested_value": suggested_value,
            "false_alarm_count": len(values),
            "status": "pending_review",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/api/baseline-suggestions")
def get_baseline_suggestions():
    with _state_lock:
        return {"suggestions": list(_baseline_suggestions.values())}


@app.post("/api/tickets/{record_id}/feedback")
def record_ticket_feedback(record_id: str, feedback: TicketFeedbackRequest):
    with _state_lock:
        record = next(
            (item for item in _demo.dashboard.records if item.record_id == record_id),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if _tickets_meta.get(record_id, {}).get("status", record.status) != "resolved":
            raise HTTPException(status_code=409, detail="Resolve the ticket before submitting caseworker feedback")
        if len(feedback.note) > 1000:
            raise HTTPException(status_code=422, detail="Feedback note must be 1000 characters or fewer")
        entry = {
            "ticket_id": record_id,
            "event_id": record.event_id,
            "subject_id": record.subject_id,
            "label": feedback.label,
            "note": feedback.note.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _ticket_feedback[record_id] = entry
        _refresh_baseline_suggestions(record.subject_id)
        return {
            "success": True,
            "feedback": entry,
            "suggestions": list(_baseline_suggestions.values()),
        }


@app.post("/api/baseline-suggestions/{subject_id}/{field}/apply")
def apply_baseline_suggestion(subject_id: str, field: str):
    with _state_lock:
        suggestion = _baseline_suggestions.get((subject_id, field))
        if suggestion is None:
            raise HTTPException(status_code=404, detail="Baseline suggestion not found")
        if suggestion["status"] != "pending_review":
            raise HTTPException(status_code=409, detail="Baseline suggestion is not pending review")
        baseline = _demo.baseline_store.get_baseline(subject_id)
        if baseline is None:
            raise HTTPException(status_code=409, detail="Resident baseline is unavailable")
        updated_baseline = dict(baseline)
        updated_baseline[field] = suggestion["suggested_value"]
        _demo.baseline_store.set_baseline(subject_id, updated_baseline)
        suggestion["status"] = "applied"
        suggestion["applied_at"] = datetime.now(timezone.utc).isoformat()
        return {"success": True, "suggestion": suggestion, "baseline": updated_baseline}


@app.patch("/api/tickets/{record_id}")
def update_ticket(record_id: str, update: TicketUpdateRequest):
    with _state_lock:
        if record_id not in _tickets_meta:
            # Check if record exists in records
            records = _demo.dashboard.records
            if not any(r.record_id == record_id for r in records):
                raise HTTPException(status_code=404, detail="Ticket not found")
            _tickets_meta[record_id] = {
                "status": "awaiting_human_review",
                "notes": "",
                "priority": "Normal",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        meta = _tickets_meta[record_id]
        if update.status is not None:
            meta["status"] = update.status
        if update.notes is not None:
            meta["notes"] = update.notes
        if update.priority is not None:
            meta["priority"] = update.priority
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        return {"success": True, "ticket_id": record_id, "meta": meta}


@app.get("/device")
def get_device_redirect():
    return RedirectResponse(url="/?view=device")


# Mount production frontend build if dist folder exists
_dist_dir = _PROJECT_ROOT / "frontend" / "dist"
if _dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(
        "aura.dashboard.server:app",
        host=host,
        port=port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    run_server()
