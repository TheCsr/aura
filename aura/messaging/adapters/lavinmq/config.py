"""LavinMQ connection and topology settings from explicit values or environment."""

from dataclasses import dataclass, field
import os
from typing import Mapping, Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class LavinMQSettings:
    url: str = field(repr=False)
    exchange: str = "aura.events"
    queue: str = "aura.sensor.events"
    routing_key: str = "sensor.event"
    prefetch_count: int = 10
    dead_letter_exchange: Optional[str] = None
    dead_letter_routing_key: Optional[str] = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"amqp", "amqps"} or not parsed.hostname:
            raise ValueError("LavinMQ URL must be an amqp:// or amqps:// URL with a host")
        for name, value in (
            ("exchange", self.exchange),
            ("queue", self.queue),
            ("routing_key", self.routing_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if (
            isinstance(self.prefetch_count, bool)
            or not isinstance(self.prefetch_count, int)
            or self.prefetch_count < 1
        ):
            raise ValueError("prefetch_count must be a positive integer")
        if self.dead_letter_routing_key and not self.dead_letter_exchange:
            raise ValueError("dead_letter_exchange is required with dead_letter_routing_key")

    @classmethod
    def from_environment(
        cls,
        environment: Optional[Mapping[str, str]] = None,
    ) -> "LavinMQSettings":
        values = os.environ if environment is None else environment
        url = values.get("LAVINMQ_URL", "").strip()
        if not url:
            raise ValueError("Set LAVINMQ_URL to the broker amqp(s) URL")
        try:
            prefetch_count = int(values.get("LAVINMQ_PREFETCH_COUNT", "10"))
        except ValueError as error:
            raise ValueError("LAVINMQ_PREFETCH_COUNT must be an integer") from error
        return cls(
            url=url,
            exchange=values.get("LAVINMQ_EXCHANGE", "aura.events"),
            queue=values.get("LAVINMQ_QUEUE", "aura.sensor.events"),
            routing_key=values.get("LAVINMQ_ROUTING_KEY", "sensor.event"),
            prefetch_count=prefetch_count,
            dead_letter_exchange=values.get("LAVINMQ_DEAD_LETTER_EXCHANGE") or None,
            dead_letter_routing_key=values.get("LAVINMQ_DEAD_LETTER_ROUTING_KEY") or None,
        )