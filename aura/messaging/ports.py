"""Messaging ports; application code depends on these, not AMQP libraries."""

from typing import Protocol

from ..contracts import InputEvent


class EventPublisher(Protocol):
    def publish(self, event: InputEvent) -> None:
        """Publish one domain event to the configured transport."""


class EventHandler(Protocol):
    def __call__(self, event: InputEvent) -> object:
        """Handle one validated event; raise to reject the delivery."""