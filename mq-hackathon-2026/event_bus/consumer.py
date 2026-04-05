"""Pure-Python event bus consumer with schema validation and deserialization.

Provides the same API as a confluent-kafka-backed consumer but uses an
in-memory message store.  When ``confluent_kafka`` is available, pass a
real ``Consumer`` instance; otherwise the built-in ``InMemoryConsumer``
is used automatically.

Requirements: 13.1, 13.4, 14.9, 14.10
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Callable, Optional

from event_bus.schemas import KAFKA_TOPICS, validate_message

logger = logging.getLogger(__name__)

DEFAULT_CONSUMER_GROUP = "mq-guardian-agent-brain"


class InMemoryMessage:
    """Minimal message object mimicking confluent_kafka.Message."""

    def __init__(self, topic: str, value: bytes) -> None:
        self._topic = topic
        self._value = value

    def topic(self) -> str:
        return self._topic

    def value(self) -> bytes:
        return self._value

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return 0

    def error(self) -> None:
        return None


class InMemoryConsumer:
    """Minimal in-memory Kafka consumer substitute.

    Works with ``InMemoryProducer`` — reads from the same in-memory
    message store.  Suitable for testing and local development.
    """

    def __init__(self) -> None:
        self._subscribed: list[str] = []
        self._messages: defaultdict[str, list[bytes]] = defaultdict(list)
        self._offsets: dict[str, int] = {}

    def inject_message(self, topic: str, value: bytes) -> None:
        """Inject a message for testing purposes."""
        self._messages[topic].append(value)

    def subscribe(self, topics: list[str], **kwargs: Any) -> None:
        self._subscribed = topics

    def poll(self, timeout: float = 1.0) -> InMemoryMessage | None:
        for topic in self._subscribed:
            offset = self._offsets.get(topic, 0)
            msgs = self._messages.get(topic, [])
            if offset < len(msgs):
                self._offsets[topic] = offset + 1
                return InMemoryMessage(topic, msgs[offset])
        return None

    def commit(self, asynchronous: bool = False) -> None:
        pass

    def close(self) -> None:
        pass


class EventBusConsumer:
    """Schema-validating event consumer.

    Accepts either a ``confluent_kafka.Consumer`` or an
    ``InMemoryConsumer``.  Deserializes JSON and validates against
    the topic's schema on every consumed message.
    """

    def __init__(
        self,
        consumer: Any | None = None,
        group_id: str = DEFAULT_CONSUMER_GROUP,
    ) -> None:
        self._consumer = consumer or InMemoryConsumer()
        self._group_id = group_id
        self._subscribed_topics: list[str] = []

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def subscribed_topics(self) -> list[str]:
        return list(self._subscribed_topics)

    def subscribe(
        self,
        topics: list[str],
        on_assign: Optional[Callable] = None,
        on_revoke: Optional[Callable] = None,
    ) -> None:
        """Subscribe to topics. Only known topics are accepted."""
        valid = [t for t in topics if t in KAFKA_TOPICS]
        unknown = [t for t in topics if t not in KAFKA_TOPICS]
        for t in unknown:
            logger.warning("Skipping unknown topic: %s", t)

        if not valid:
            logger.warning("No valid topics to subscribe to.")
            return

        self._subscribed_topics = valid
        kwargs: dict[str, Any] = {}
        if on_assign is not None:
            kwargs["on_assign"] = on_assign
        if on_revoke is not None:
            kwargs["on_revoke"] = on_revoke
        self._consumer.subscribe(valid, **kwargs)
        logger.info("Consumer group '%s' subscribed to: %s", self._group_id, valid)

    def poll_one(
        self, timeout: float = 1.0,
    ) -> Optional[tuple[str, dict[str, Any], list[str]]]:
        """Poll one message, deserialize, validate. Returns (topic, event, errors) or None."""
        msg = self._consumer.poll(timeout)
        if msg is None:
            return None

        # Handle confluent_kafka error protocol
        err = getattr(msg, "error", lambda: None)()
        if err is not None:
            code = getattr(err, "code", lambda: None)()
            # _PARTITION_EOF = -191
            if code == -191:
                return None
            # Re-raise as a generic exception for other errors
            raise RuntimeError(f"Kafka error: {err}")

        topic = msg.topic()
        raw_value = msg.value()

        try:
            event = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError) as exc:
            return topic, {}, [f"JSON deserialization error: {exc}"]

        _ok, errors = validate_message(topic, event)
        if errors:
            logger.warning("Schema validation errors on %s: %s", topic, errors)

        return topic, event, errors

    def consume_loop(
        self,
        handler: Callable[[str, dict[str, Any], list[str]], None],
        *,
        poll_timeout: float = 1.0,
        max_messages: Optional[int] = None,
    ) -> int:
        """Continuously poll and dispatch messages to handler."""
        count = 0
        try:
            while max_messages is None or count < max_messages:
                result = self.poll_one(timeout=poll_timeout)
                if result is None:
                    if max_messages is not None:
                        # Avoid infinite loop in bounded mode
                        break
                    continue
                topic, event, errors = result
                handler(topic, event, errors)
                count += 1
        except KeyboardInterrupt:
            logger.info("Consumer loop interrupted.")
        return count

    def commit(self, asynchronous: bool = False) -> None:
        self._consumer.commit(asynchronous=asynchronous)

    def close(self) -> None:
        self._consumer.close()
        logger.info("Consumer group '%s' closed.", self._group_id)
