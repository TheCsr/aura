"""Versioned JSON encoding for domain events sent over AMQP."""

from datetime import datetime
import json
from typing import Any, Dict, Mapping, Optional

from ..contracts import InputEvent, Transcript


class InvalidEventMessage(ValueError):
    """Raised when a broker message cannot be decoded as a supported event."""


class EventMessageCodec:
    SCHEMA_VERSION = "1.0"

    @classmethod
    def encode(cls, event: InputEvent) -> bytes:
        if event.schema_version != cls.SCHEMA_VERSION:
            raise InvalidEventMessage(
                f"Unsupported event schema version: {event.schema_version}"
            )
        transcript: Optional[Dict[str, Optional[str]]] = None
        if event.transcript is not None:
            transcript = {
                "text": event.transcript.text,
                "source": event.transcript.source,
                "language": event.transcript.language,
                "model": event.transcript.model,
            }
        payload = {
            "schema_version": event.schema_version,
            "event_id": event.event_id,
            "occurred_at": event.occurred_at.isoformat(),
            "subject_id": event.subject_id,
            "source": event.source,
            "observations": dict(event.observations),
            "transcript": transcript,
        }
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise InvalidEventMessage("Event is not JSON serializable") from error

    @classmethod
    def decode(cls, body: bytes) -> InputEvent:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidEventMessage("Message body must be UTF-8 JSON") from error

        if not isinstance(payload, dict):
            raise InvalidEventMessage("Message body must be a JSON object")
        required_keys = {
            "schema_version",
            "event_id",
            "occurred_at",
            "subject_id",
            "source",
            "observations",
            "transcript",
        }
        if set(payload) != required_keys:
            raise InvalidEventMessage("Message has an unexpected event schema")
        if payload["schema_version"] != cls.SCHEMA_VERSION:
            raise InvalidEventMessage(
                f"Unsupported event schema version: {payload['schema_version']}"
            )
        if not isinstance(payload["occurred_at"], str):
            raise InvalidEventMessage("occurred_at must be an ISO-8601 string")
        if not isinstance(payload["observations"], Mapping):
            raise InvalidEventMessage("observations must be a JSON object")

        transcript_payload = payload["transcript"]
        transcript = None
        if transcript_payload is not None:
            transcript = cls._decode_transcript(transcript_payload)

        try:
            occurred_at = datetime.fromisoformat(payload["occurred_at"])
            return InputEvent(
                event_id=payload["event_id"],
                occurred_at=occurred_at,
                subject_id=payload["subject_id"],
                source=payload["source"],
                observations=payload["observations"],
                transcript=transcript,
                schema_version=payload["schema_version"],
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise InvalidEventMessage("Event fields failed validation") from error

    @staticmethod
    def _decode_transcript(payload: Any) -> Transcript:
        if not isinstance(payload, dict):
            raise InvalidEventMessage("transcript must be an object or null")
        allowed_keys = {"text", "source", "language", "model"}
        if not {"text", "source"}.issubset(payload) or not set(payload).issubset(allowed_keys):
            raise InvalidEventMessage("transcript has an unexpected schema")
        if not isinstance(payload["text"], str) or not isinstance(payload["source"], str):
            raise InvalidEventMessage("transcript text and source must be strings")
        for key in ("language", "model"):
            if payload.get(key) is not None and not isinstance(payload[key], str):
                raise InvalidEventMessage(f"transcript {key} must be a string or null")
        try:
            return Transcript(
                text=payload["text"],
                source=payload["source"],
                language=payload.get("language"),
                model=payload.get("model"),
            )
        except ValueError as error:
            raise InvalidEventMessage("Transcript fields failed validation") from error