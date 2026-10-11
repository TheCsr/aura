"""Publish synthetic sensor events or consume them through the full demo workflow."""

import argparse
from datetime import datetime, timezone
import json
import logging
import math
import time
from typing import Any, Dict, Optional, Sequence
from uuid import uuid4

from ..contracts import InputEvent
from ..messaging.adapters.lavinmq import (
    LavinMQSettings,
    PikaLavinMQConsumer,
    PikaLavinMQPublisher,
)
from ..sensors.application import ValidatedSensorEventHandler, ValidatedSensorEventPublisher
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


def result_as_json(result: StreamingResult) -> str:
    payload = {
        "event_id": result.event_id,
        "outcome": result.outcome.value,
        "supervisor": {
            "is_anomaly": result.assessment.is_anomaly,
            "requires_review": result.assessment.requires_review,
            "summary": result.assessment.summary,
            "confidence": result.assessment.confidence,
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
            result.receipt.external_reference if result.receipt is not None else None
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
        ValidatedSensorEventHandler(handle_event),
    )
    try:
        consumer.run_forever()
    except KeyboardInterrupt:
        consumer.stop()


def _publish(args: argparse.Namespace) -> None:
    if not math.isfinite(args.interval) or args.interval < 0:
        raise ValueError("--interval must be a finite, non-negative number")
    settings = LavinMQSettings.from_environment()
    publisher = PikaLavinMQPublisher(settings)
    validated_publisher = ValidatedSensorEventPublisher(publisher)
    try:
        for index in range(args.count):
            event = make_sensor_event(args.scenario)
            validated_publisher.publish(event)
            print(
                json.dumps(
                    {
                        "published_event_id": event.event_id,
                        "scenario": args.scenario,
                        "sequence": index + 1,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if index + 1 < args.count and args.interval:
                time.sleep(args.interval)
    finally:
        publisher.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AURA LavinMQ streaming demo.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("consume", help="consume and process sensor events")
    publish_parser = commands.add_parser("publish", help="publish synthetic sensor events")
    publish_parser.add_argument("--scenario", choices=SCENARIOS, default="critical-cold")
    publish_parser.add_argument("--count", type=int, default=1)
    publish_parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "consume":
        _consume()
    else:
        if args.count < 1:
            parser.error("--count must be at least 1")
        _publish(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())