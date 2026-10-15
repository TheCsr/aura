"""Publish synthetic sensor events or consume them through the full demo workflow."""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
from uuid import uuid4

from ..contracts import InputEvent
from ..messaging.adapters.lavinmq import (
    LavinMQSettings,
    PikaLavinMQConsumer,
    PikaLavinMQPublisher,
)
from ..sensors.application import ValidatedSensorEventPublisher
from .composition import DEFAULT_SUBJECT_ID, create_streaming_demo
from .service import StreamingResult


logger = logging.getLogger("aura.streaming.demo")
SCENARIOS = ("normal", "critical-cold", "door-open")


def make_sensor_event(scenario: str, *, event_id: Optional[str] = None) -> InputEvent:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of: {', '.join(SCENARIOS)}")
    observations: Dict[str, Any] = {
        "temperature_c": 18.0,
        "humidity_pct": 45.0,
        "door_state": "closed",
        "motion_detected": True,
    }
    if scenario == "critical-cold":
        observations.update(
            temperature_c=-20.0,
            door_state="open",
            motion_detected=False,
        )
    elif scenario == "door-open":
        observations.update(door_state="open", motion_detected=False)
    return InputEvent(
        event_id=event_id or f"sensor-{scenario}-{uuid4().hex}",
        occurred_at=datetime.now(timezone.utc),
        subject_id=DEFAULT_SUBJECT_ID,
        source="synthetic-sensor-publisher",
        observations=observations,
    )


def make_terminal_sensor_event(args: argparse.Namespace) -> InputEvent:
    """Build an event from readings explicitly supplied by a sensor operator."""
    observations: Dict[str, Any] = {
        "temperature_c": args.temperature_c,
        "humidity_pct": args.humidity_pct,
        "door_state": args.door_state,
        "motion_detected": args.motion_detected == "true",
    }
    return InputEvent(
        event_id=args.event_id or f"sensor-{uuid4().hex}",
        occurred_at=datetime.now(timezone.utc),
        subject_id=args.subject_id,
        source=args.source,
        observations=observations,
    )


def _load_dotenv_if_present() -> None:
    """Load unset environment values from the repository .env without displaying them."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def result_as_json(result: StreamingResult) -> str:
    ticket_actions = {
        "request_human_review",
        "maintenance_ticket",
        "remote_followup",
        "home_visit",
        "urgent_visit",
    }
    is_ticket_action = (
        result.proposal is not None
        and result.proposal.action_type in ticket_actions
    )
    payload = {
        "event_id": result.event_id,
        "outcome": result.outcome.value,
        "supervisor": {
            "is_anomaly": result.assessment.is_anomaly,
            "requires_review": result.assessment.requires_review,
            "summary": result.assessment.summary,
            "confidence": result.assessment.confidence,
            "drop_reason": result.assessment.drop_reason,
            "severity": result.assessment.severity.value if result.assessment.severity else None,
            "evidence": [
                {"source": item.source, "description": item.description, "details": dict(item.details)}
                for item in result.assessment.evidence
            ],
        },
        "voice_prompt": (
            {
                "event_id": result.voice_prompt.event_id,
                "conversation_id": result.voice_prompt.conversation_id,
                "text": result.voice_prompt.text,
                "output_mode": result.voice_prompt.speaker_receipt.output_mode,
                "delivered": result.voice_prompt.speaker_receipt.delivered,
            }
            if result.voice_prompt is not None
            else None
        ),
        "resident_reply": (
            {
                "event_id": result.voice_reply.event_id,
                "conversation_id": result.voice_reply.conversation_id,
                "transcript": result.voice_reply.transcript.text,
                "transcript_source": result.voice_reply.transcript.source,
                "signal_analysis_source": result.voice_reply.signal_analysis.source,
                "acoustic_features": result.voice_reply.signal_analysis.features,
            }
            if result.voice_reply is not None
            else None
        ),
        "triage": (
            {
                "event_id": result.proposal.event_id,
                "proposal_id": result.proposal.proposal_id,
                "action_type": result.proposal.action_type,
                "rationale": result.proposal.rationale,
                "confidence": result.proposal.confidence,
                "parameters": dict(result.proposal.parameters),
                "evidence_refs": list(result.proposal.evidence_refs),
                "triage_stage": result.proposal.triage_stage.value,
            } if result.proposal is not None else None
        ),
        "triage_passes": [
            {
                "event_id": proposal.event_id,
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "rationale": proposal.rationale,
                "confidence": proposal.confidence,
                "parameters": dict(proposal.parameters),
                "evidence_refs": list(proposal.evidence_refs),
                "triage_stage": proposal.triage_stage.value,
            }
            for proposal in result.triage_passes
        ],
        "dispatcher": (
            {
                "status": result.receipt.status.value,
                "message": result.receipt.message,
                "external_reference": result.receipt.external_reference,
            } if result.receipt is not None else None
        ),
        "guardian": (
            {
                "status": result.decision.status.value,
                "reason_codes": list(result.decision.reason_codes),
                "rationale": result.decision.rationale,
            }
            if result.decision is not None
            else None
        ),
        "dashboard_record_id": (
            result.receipt.external_reference
            if is_ticket_action and result.receipt is not None
            else None
        ),
        "action_record_id": (
            result.receipt.external_reference
            if result.proposal is not None
            and result.proposal.action_type not in ticket_actions
            and result.receipt is not None
            else None
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)


def _consume() -> None:
    demo = create_streaming_demo()

    def handle_event(event: InputEvent) -> StreamingResult:
        result = demo.service.handle(event)
        print(result_as_json(result), flush=True)
        if result.receipt is not None:
            print(
                f"Dashboard records in this process: {len(demo.dashboard.records)}",
                flush=True,
            )
        return result

    settings = LavinMQSettings.from_environment()
    consumer = PikaLavinMQConsumer(
        settings,
        handle_event,
    )
    try:
        consumer.run_forever()
    except KeyboardInterrupt:
        consumer.stop()


def _publish(args: argparse.Namespace) -> None:
    settings = LavinMQSettings.from_environment()
    publisher = PikaLavinMQPublisher(settings)
    validated_publisher = ValidatedSensorEventPublisher(publisher)
    event = make_terminal_sensor_event(args)
    try:
        validated_publisher.publish(event)
        print(
            json.dumps(
                {
                    "published_event_id": event.event_id,
                    "source": event.source,
                    "subject_id": event.subject_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "observations": dict(event.observations),
                    "broker_queue": settings.queue,
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            flush=True,
        )
    finally:
        publisher.close()


def _run(args: argparse.Namespace) -> None:
    demo = create_streaming_demo(resident_response=args.resident_response)
    event = make_sensor_event(args.scenario)
    result = demo.service.handle(event)
    print(result_as_json(result), flush=True)
    if result.receipt is not None:
        print(
            f"Dashboard records created: {len(demo.dashboard.records)}",
            flush=True,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Send sensor events through the AURA streaming pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("consume", help="consume and process sensor events via LavinMQ")
    publish_parser = commands.add_parser(
        "publish",
        help="publish real sensor readings supplied as command-line values to LavinMQ",
    )
    publish_parser.add_argument("--temperature-c", type=float, required=True)
    publish_parser.add_argument("--humidity-pct", type=float, required=True)
    publish_parser.add_argument("--door-state", choices=("open", "closed", "unknown"), required=True)
    publish_parser.add_argument("--motion-detected", choices=("true", "false"), required=True)
    publish_parser.add_argument("--subject-id", default=DEFAULT_SUBJECT_ID)
    publish_parser.add_argument("--source", default="terminal-sensor")
    publish_parser.add_argument("--event-id")

    run_parser = commands.add_parser("run", help="run a sensor scenario locally without a broker")
    run_parser.add_argument("--scenario", choices=SCENARIOS, default="critical-cold")
    run_parser.add_argument(
        "--resident-response",
        default="Jag är okej, men jag vill gärna prata med personalen.",
        help="transcript text returned by mock STT for resident voice check",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_dotenv_if_present()
    if args.command == "consume":
        _consume()
    elif args.command == "run":
        _run(args)
    else:
        _publish(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())