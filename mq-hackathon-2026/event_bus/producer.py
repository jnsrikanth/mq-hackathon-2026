"""Pure-Python event bus producer with schema validation and dead-letter routing.

Provides the same API as a confluent-kafka-backed producer but uses an
in-memory message store.  When ``confluent_kafka`` is available, pass a
real ``Producer`` instance; otherwise the built-in ``InMemoryProducer``
is used automatically.

Requirements: 13.2, 13.3, 13.5
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from event_bus.schemas import KAFKA_TOPICS, validate_message

logger = logging.getLogger(__name__)

DEAD_LETTER_TOPIC = "mq-dead-letter"


class InMemoryProducer:
    """Minimal in-memory Kafka producer substitute.

    Messages are stored in ``produced_messages`` keyed by topic for
    testing and local development.  Drop-in replacement for
    ``confluent_kafka.Producer`` at the interface level we use.
    """

    def __init__(self) -> None:
        self.produced_messages: defaultdict[str, list[bytes]] = defaultdict(list)

    def produce(self, topic: str, *, value: bytes, callback: Any = None) -> None:
        self.produced_messages[topic].append(value)

    def poll(self, timeout: float = 0) -> int:
        return 0

    def flush(self, timeout: float = 10.0) -> int:
        return 0


class EventBusProducer:
    """Schema-validating event producer.

    Accepts either a ``confluent_kafka.Producer`` or an
    ``InMemoryProducer``.  Validates every message before producing and
    routes invalid messages to the dead-letter topic.
    """

    def __init__(
        self,
        producer: Any | None = None,
        on_delivery: Optional[Callable] = None,
    ) -> None:
        self._producer = producer or InMemoryProducer()
        self._on_delivery = on_delivery or self._default_on_delivery

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, topic: str, event: dict[str, Any]) -> bool:
        """Validate *event* and produce it.  Returns True on success."""
        ok, errors = validate_message(topic, event)
        if not ok:
            self._route_to_dead_letter(topic, event, errors)
            return False
        self._produce(topic, event)
        return True

    def publish_with_correlation(
        self, topic: str, event: dict[str, Any], correlation_id: str,
    ) -> bool:
        """Attach *correlation_id* then publish (Req 13.5)."""
        event["correlation_id"] = correlation_id
        return self.publish(topic, event)

    def flush(self, timeout: float = 10.0) -> int:
        return self._producer.flush(timeout)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _produce(self, topic: str, event: dict[str, Any]) -> None:
        value = json.dumps(event).encode("utf-8")
        self._producer.produce(topic, value=value, callback=self._on_delivery)
        self._producer.poll(0)

    def _route_to_dead_letter(
        self, original_topic: str, original_event: dict[str, Any], errors: list[str],
    ) -> None:
        dl_event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "correlation_id": original_event.get("correlation_id", str(uuid.uuid4())),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "dead_letter",
            "payload": {
                "original_topic": original_topic,
                "original_event": original_event,
                "validation_errors": errors,
            },
        }
        logger.warning(
            "Schema validation failed for topic %s — routing to %s: %s",
            original_topic, DEAD_LETTER_TOPIC, errors,
        )
        self._produce(DEAD_LETTER_TOPIC, dl_event)

    @staticmethod
    def _default_on_delivery(err: Any, msg: Any) -> None:
        if err is not None:
            logger.error("Delivery failed: %s", err)
