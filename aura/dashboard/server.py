"""FastAPI backend server for the AURA React Dashboard."""

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List, Optional
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
from aura.sensors.application import ValidatedSensorEventHandler, ValidatedSensorEventPublisher
from aura.streaming.composition import DEFAULT_SUBJECT_ID, create_streaming_demo
from aura.streaming.demo import make_sensor_event, result_as_json
from aura.streaming.service import StreamingResult
from aura.voice.adapters.fake_stt import FakeSpeechToTextAdapter
from aura.voice.adapters.google_tts import GoogleTranslateTtsAdapter
from aura.voice.adapters.orange_kb_whisper import OrangeKbWhisperAdapter
from aura.voice.adapters.wav_signal_analyzer import PcmWavSignalAnalyzer
from aura.voice.contracts import AudioRecording

logger = logging.getLogger("aura.dashboard.server")


@asynccontextmanager
async def _dashboard_lifespan(_app: FastAPI):
    _start_broker_consumer()
    try:
        yield
    finally:
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

# Active prompt for the Resident Device
_active_prompt: Optional[Dict[str, Any]] = None
_pending_device_event: Optional[InputEvent] = None
_google_tts = GoogleTranslateTtsAdapter(default_language="sv")
_kb_whisper = OrangeKbWhisperAdapter()
_signal_analyzer = PcmWavSignalAnalyzer()
_broker_consumer: Optional[PikaLavinMQConsumer] = None
_broker_consumer_thread: Optional[Thread] = None


def _handle_broker_event(event: InputEvent) -> StreamingResult:
    with _state_lock:
        result = _demo.service.handle(event)
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
        ValidatedSensorEventHandler(_handle_broker_event),
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


def _serialize_result(result: StreamingResult) -> Dict[str, Any]:
    payload = json.loads(result_as_json(result))
    sensor_data = _event_sensor_data.get(result.event_id)
    if sensor_data is not None:
        payload["sensor_data"] = sensor_data
    return payload


def _record_result(result: StreamingResult, event: InputEvent) -> None:
    global _latest_result, _active_prompt
    _latest_result = result
    _event_sensor_data[result.event_id] = {
        "observations": dict(event.observations),
        "source": event.source,
        "subject_id": event.subject_id,
        "occurred_at": event.occurred_at.isoformat(),
        "is_mock": event.source.startswith("synthetic-") or "simulator" in event.source,
    }
    # Check if duplicate event_id already stored
    if not any(r.event_id == result.event_id for r in _processed_results):
        _processed_results.insert(0, result)

    if result.voice_prompt is not None:
        _active_prompt = {
            "event_id": result.voice_prompt.event_id,
            "conversation_id": result.voice_prompt.conversation_id,
            "text": result.voice_prompt.text,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending_reply",
        }

    if result.receipt is not None and result.receipt.external_reference:
        rec_id = result.receipt.external_reference
        if rec_id not in _tickets_meta:
            _tickets_meta[rec_id] = {
                "status": "awaiting_human_review",
                "notes": "",
                "priority": "High" if "critical" in result.event_id else "Normal",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }


class SimulateRequest(BaseModel):
    scenario: Optional[str] = "critical-cold"
    resident_response: Optional[str] = "Jag är okej, men jag vill gärna prata med personalen."
    custom_observations: Optional[Dict[str, Any]] = None


class PublishRequest(BaseModel):
    scenario: Optional[str] = "critical-cold"
    custom_observations: Optional[Dict[str, Any]] = None


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    priority: Optional[str] = None


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


@app.get("/api/latest")
def get_latest():
    with _state_lock:
        if _latest_result is None:
            return {"latest": None}
        return {"latest": _serialize_result(_latest_result)}


@app.get("/api/events")
def get_events(limit: int = 50):
    with _state_lock:
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

        if req.resident_response:
            _demo.service._voice_responder._transcriber = FakeSpeechToTextAdapter(req.resident_response)

        result = _demo.service.handle(event)
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
                result = _demo.service.handle(event)
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


class DeviceRespondRequest(BaseModel):
    text_response: Optional[str] = None
    audio_base64: Optional[str] = None


@app.get("/api/device/prompt")
def get_device_prompt():
    with _state_lock:
        global _active_prompt
        if _active_prompt is None:
            if _latest_result is not None and _latest_result.voice_prompt is not None:
                _active_prompt = {
                    "event_id": _latest_result.voice_prompt.event_id,
                    "conversation_id": _latest_result.voice_prompt.conversation_id,
                    "text": _latest_result.voice_prompt.text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "pending_reply",
                }
        return {
            "prompt": _active_prompt,
            "whisper_configured": _kb_whisper.is_configured,
        }


@app.post("/api/device/trigger-prompt")
def trigger_device_prompt(req: DeviceTriggerRequest):
    with _state_lock:
        global _active_prompt, _pending_device_event
        if req.custom_observations:
            event = InputEvent(
                event_id=f"sensor-device-{uuid4().hex[:8]}",
                occurred_at=datetime.now(timezone.utc),
                subject_id=DEFAULT_SUBJECT_ID,
                source="resident-device-trigger",
                observations=req.custom_observations,
            )
        else:
            event = make_sensor_event(req.scenario or "critical-cold", event_id=f"sensor-device-{uuid4().hex[:8]}")

        _pending_device_event = event
        assessment = _demo.service._supervisor(event)
        if not assessment.is_anomaly and not assessment.requires_review:
            return {
                "success": False,
                "message": "Supervisor evaluated readings as normal. No resident prompt required.",
                "outcome": "no_action",
            }

        prompt_text = (
            "Hej. Vi har märkt en avvikelse i sensorerna hemma hos dig. Är allt bra? Berätta gärna om du behöver hjälp."
        )
        conv_id = uuid4().hex
        _active_prompt = {
            "event_id": event.event_id,
            "conversation_id": conv_id,
            "text": prompt_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending_reply",
            "observations": dict(event.observations),
        }
        return {
            "success": True,
            "prompt": _active_prompt,
            "whisper_configured": _kb_whisper.is_configured,
        }


@app.get("/api/device/tts")
def get_device_tts(text: Optional[str] = None):
    speech_text = text or (_active_prompt.get("text") if _active_prompt else "Hej, vi har märkt en avvikelse. Hur mår du?")
    try:
        audio_bytes = _google_tts.synthesize(speech_text, language="sv")
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"},
        )
    except Exception as error:
        logger.error("TTS generation failed: %s", error)
        raise HTTPException(status_code=500, detail=f"Google TTS generation error: {error}")


@app.post("/api/device/respond")
async def respond_from_device(
    text_response: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
):
    global _active_prompt, _pending_device_event

    transcript_text = ""
    stt_source = "manual_text"
    audio_bytes: Optional[bytes] = None

    if audio_file is not None:
        audio_bytes = await audio_file.read()

    # Transcribe with KB-Whisper if audio provided
    if audio_bytes and len(audio_bytes) > 0:
        recording = AudioRecording(
            audio=audio_bytes,
            filename=audio_file.filename if audio_file else "audio.wav",
            content_type=audio_file.content_type if audio_file else "audio/wav",
        )
        if _kb_whisper.is_configured:
            try:
                t_res = _kb_whisper.transcribe(recording, language="sv")
                transcript_text = t_res.text
                stt_source = "orange-kb-whisper"
            except Exception as err:
                logger.warning("KB-Whisper transcription failed, using fallback: %s", err)
                transcript_text = text_response or "Jag ramlade och behöver hjälp med en gång."
                stt_source = f"fallback-error: {err}"
        else:
            transcript_text = text_response or "Jag mår bra men känner mig lite kall."
            stt_source = "fallback-no-api-key"
    else:
        transcript_text = text_response or "Jag är okej, tack för omtanken."

    with _state_lock:
        # Use pending event or make new critical-cold event if none pending
        event = _pending_device_event or make_sensor_event("critical-cold")
        _demo.service._voice_responder._transcriber = FakeSpeechToTextAdapter(transcript_text)
        result = _demo.service.handle(event)
        _record_result(result, event)

        if _active_prompt:
            _active_prompt["status"] = "answered"
            _active_prompt["resident_reply"] = transcript_text

        ticket_id = result.receipt.external_reference if result.receipt is not None else None

        return {
            "success": True,
            "transcript": transcript_text,
            "stt_source": stt_source,
            "outcome": result.outcome.value,
            "ticket_id": ticket_id,
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
                "status": "awaiting_human_review",
                "notes": "",
                "priority": "High" if "critical" in r.event_id else "Normal",
                "updated_at": r.created_at.isoformat(),
            })
            tickets_list.append({
                "record_id": r.record_id,
                "event_id": r.event_id,
                "proposal_id": r.proposal_id,
                "subject_id": r.subject_id,
                "created_at": r.created_at.isoformat(),
                "status": meta.get("status", "awaiting_human_review"),
                "notes": meta.get("notes", ""),
                "priority": meta.get("priority", "Normal"),
                "summary": r.summary,
                "observations": dict(r.observations),
            })
        return {"tickets": tickets_list}


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
