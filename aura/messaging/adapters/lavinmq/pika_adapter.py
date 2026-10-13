"""Pika BlockingConnection adapters for LavinMQ's AMQP 0-9-1 interface."""

import logging
import threading
from typing import Any, Callable, Optional

import pika

from ....contracts import InputEvent
from ...codec import EventMessageCodec, InvalidEventMessage
from ...ports import EventHandler, EventPublisher
from .config import LavinMQSettings


logger = logging.getLogger(__name__)


def _declare_topology(channel: Any, settings: LavinMQSettings) -> None:
    channel.exchange_declare(
        exchange=settings.exchange,
        exchange_type="direct",
        durable=True,
    )
    dead_letter_arguments = None
    if settings.dead_letter_exchange:
        dead_letter_queue = settings.dead_letter_queue or f"{settings.queue}.dead"
        dead_letter_routing_key = (
            settings.dead_letter_routing_key or settings.routing_key
        )
        channel.exchange_declare(
            exchange=settings.dead_letter_exchange,
            exchange_type="direct",
            durable=True,
        )
        channel.queue_declare(queue=dead_letter_queue, durable=True)
        channel.queue_bind(
            exchange=settings.dead_letter_exchange,
            queue=dead_letter_queue,
            routing_key=dead_letter_routing_key,
        )
        dead_letter_arguments = {"x-dead-letter-exchange": settings.dead_letter_exchange}
        dead_letter_arguments["x-dead-letter-routing-key"] = dead_letter_routing_key
    channel.queue_declare(
        queue=settings.queue,
        durable=True,
        arguments=dead_letter_arguments,
    )
    channel.queue_bind(
        exchange=settings.exchange,
        queue=settings.queue,
        routing_key=settings.routing_key,
    )


class PikaLavinMQPublisher(EventPublisher):
    """Publish persistent, confirmed events to a durable LavinMQ queue."""

    def __init__(
        self,
        settings: LavinMQSettings,
        connection_factory: Callable[[Any], Any] = pika.BlockingConnection,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._connection: Optional[Any] = None
        self._channel: Optional[Any] = None

    def connect(self) -> None:
        if self._connection is not None and self._connection.is_open:
            return
        connection = self._connection_factory(pika.URLParameters(self._settings.url))
        try:
            channel = connection.channel()
            _declare_topology(channel, self._settings)
            channel.confirm_delivery()
        except Exception:
            if connection.is_open:
                connection.close()
            raise
        self._connection = connection
        self._channel = channel

    def publish(self, event: InputEvent) -> None:
        if self._channel is None or not self._channel.is_open:
            self.connect()
        if self._channel is None:
            raise RuntimeError("LavinMQ publisher failed to open a channel")
        confirmed = self._channel.basic_publish(
            exchange=self._settings.exchange,
            routing_key=self._settings.routing_key,
            body=EventMessageCodec.encode(event),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
                message_id=event.event_id,
                timestamp=int(event.occurred_at.timestamp()),
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise RuntimeError(f"LavinMQ did not confirm event {event.event_id}")

    def close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None

    def __enter__(self) -> "PikaLavinMQPublisher":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


class PikaLavinMQConsumer:
    """Consume validated events and ack only after the application handler succeeds.

    Invalid messages and handler failures are nacked without requeue. Configure a
    dead-letter exchange to retain them; without one, the broker discards them.
    """

    def __init__(
        self,
        settings: LavinMQSettings,
        handler: EventHandler,
        connection_factory: Callable[[Any], Any] = pika.BlockingConnection,
    ) -> None:
        self._settings = settings
        self._handler = handler
        self._connection_factory = connection_factory
        self._connection: Optional[Any] = None
        self._channel: Optional[Any] = None
        self._stop_requested = threading.Event()

    def connect(self) -> None:
        if self._connection is not None and self._connection.is_open:
            return
        connection = self._connection_factory(pika.URLParameters(self._settings.url))
        try:
            channel = connection.channel()
            _declare_topology(channel, self._settings)
            channel.basic_qos(prefetch_count=self._settings.prefetch_count)
            channel.basic_consume(
                queue=self._settings.queue,
                on_message_callback=self._on_delivery,
                auto_ack=False,
            )
        except Exception:
            if connection.is_open:
                connection.close()
            raise
        self._connection = connection
        self._channel = channel

    def run_forever(self, reconnect_delay_seconds: float = 3.0) -> None:
        if reconnect_delay_seconds <= 0:
            raise ValueError("reconnect_delay_seconds must be positive")
        self._stop_requested.clear()
        while not self._stop_requested.is_set():
            try:
                self.connect()
                if self._channel is not None:
                    self._channel.start_consuming()
            except KeyboardInterrupt:
                self._stop_requested.set()
            except (pika.exceptions.AMQPError, OSError):
                logger.warning("LavinMQ consumer connection failed; retrying")
            finally:
                self._close_connection()
            if not self._stop_requested.is_set():
                self._stop_requested.wait(reconnect_delay_seconds)

    def stop(self) -> None:
        self._stop_requested.set()
        if self._connection is not None and self._connection.is_open:
            try:
                self._connection.add_callback_threadsafe(self._stop_consuming)
            except pika.exceptions.AMQPError:
                self._close_connection()

    def _stop_consuming(self) -> None:
        if self._channel is not None and self._channel.is_open:
            self._channel.stop_consuming()

    def _close_connection(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None

    def _on_delivery(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        try:
            if getattr(properties, "content_type", None) != "application/json":
                raise InvalidEventMessage("AMQP content_type must be application/json")
            event = EventMessageCodec.decode(body)
            message_id = getattr(properties, "message_id", None)
            if message_id is not None and message_id != event.event_id:
                raise InvalidEventMessage("AMQP message_id does not match event_id")
            self._handler(event)
        except InvalidEventMessage as error:
            logger.warning("Rejecting invalid LavinMQ event: %s", error)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            logger.exception("Event handler failed; rejecting LavinMQ delivery")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        else:
            channel.basic_ack(delivery_tag=method.delivery_tag)