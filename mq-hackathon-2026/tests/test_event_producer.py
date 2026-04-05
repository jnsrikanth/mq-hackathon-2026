"""Tests for event_bus.producer — EventBusProducer wrapper."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from event_bus.producer import DEAD_LETTER_TOPIC, EventBusProducer, InMemoryProducer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_producer() -> tuple[EventBusProducer, MagicMock]:
    mock_kafka = MagicMock()
    mock_kafka.flush.return_value = 0
    producer = EventBusProducer(producer=mock_kafka)
    return producer, mock_kafka


def _base_event(**overrides) -> dict:
    msg = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "test_event",
        "payload": {},
    }
    msg.update(overrides)
    return msg


# ---------------------------------------------------------------------------
# InMemoryProducer
# ---------------------------------------------------------------------------

class TestInMemoryProducer:
    def test_produce_stores_message(self):
        p = InMemoryProducer()
        p.produce("test-topic", value=b'{"key": "val"}')
        assert len(p.produced_messages["test-topic"]) == 1

    def test_default_constructor_uses_in_memory(self):
        producer = EventBusProducer()
        assert isinstance(producer._producer, InMemoryProducer)


# ---------------------------------------------------------------------------
# publish — valid messages
# ---------------------------------------------------------------------------

class TestPublishValid:
    def test_publish_valid_message_returns_true(self):
        producer, mock_kafka = _make_producer()
        result = producer.publish("mq-telemetry-raw", _base_event())
        assert result is True

    def test_publish_calls_kafka_produce(self):
        producer, mock_kafka = _make_producer()
        event = _base_event()
        producer.publish("mq-telemetry-raw", event)
        mock_kafka.produce.assert_called_once()
        args, kwargs = mock_kafka.produce.call_args
        assert args[0] == "mq-telemetry-raw"
        assert json.loads(kwargs["value"]) == event

    def test_publish_polls_after_produce(self):
        producer, mock_kafka = _make_producer()
        producer.publish("mq-telemetry-raw", _base_event())
        mock_kafka.poll.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# publish — invalid messages (dead-letter routing)
# ---------------------------------------------------------------------------

class TestPublishInvalid:
    def test_publish_invalid_returns_false(self):
        producer, mock_kafka = _make_producer()
        result = producer.publish("mq-telemetry-raw", {})
        assert result is False

    def test_publish_invalid_routes_to_dead_letter(self):
        producer, mock_kafka = _make_producer()
        bad_event = {"not": "valid"}
        producer.publish("mq-telemetry-raw", bad_event)

        assert mock_kafka.produce.call_count == 1
        dl_call = mock_kafka.produce.call_args_list[0]
        assert dl_call[0][0] == DEAD_LETTER_TOPIC

        dl_payload = json.loads(dl_call[1]["value"])
        assert dl_payload["event_type"] == "dead_letter"
        assert dl_payload["payload"]["original_topic"] == "mq-telemetry-raw"
        assert dl_payload["payload"]["original_event"] == bad_event
        assert len(dl_payload["payload"]["validation_errors"]) > 0

    def test_publish_unknown_topic_routes_to_dead_letter(self):
        producer, mock_kafka = _make_producer()
        result = producer.publish("nonexistent-topic", _base_event())
        assert result is False
        dl_call = mock_kafka.produce.call_args_list[0]
        assert dl_call[0][0] == DEAD_LETTER_TOPIC


# ---------------------------------------------------------------------------
# publish_with_correlation
# ---------------------------------------------------------------------------

class TestPublishWithCorrelation:
    def test_attaches_correlation_id(self):
        producer, mock_kafka = _make_producer()
        event = _base_event()
        cid = str(uuid.uuid4())
        producer.publish_with_correlation("mq-audit-log", event, cid)
        produced_value = json.loads(mock_kafka.produce.call_args[1]["value"])
        assert produced_value["correlation_id"] == cid

    def test_overwrites_existing_correlation_id(self):
        producer, mock_kafka = _make_producer()
        event = _base_event(correlation_id="old-id")
        new_cid = str(uuid.uuid4())
        producer.publish_with_correlation("mq-audit-log", event, new_cid)
        produced_value = json.loads(mock_kafka.produce.call_args[1]["value"])
        assert produced_value["correlation_id"] == new_cid

    def test_returns_true_on_valid(self):
        producer, _ = _make_producer()
        result = producer.publish_with_correlation(
            "mq-audit-log", _base_event(), str(uuid.uuid4())
        )
        assert result is True

    def test_returns_false_on_invalid(self):
        producer, _ = _make_producer()
        result = producer.publish_with_correlation(
            "mq-audit-log", {"bad": "msg"}, str(uuid.uuid4())
        )
        assert result is False

    def test_correlation_id_present_in_produced_message(self):
        producer, mock_kafka = _make_producer()
        event = _base_event()
        del event["correlation_id"]
        cid = str(uuid.uuid4())
        producer.publish_with_correlation("mq-audit-log", event, cid)
        produced_value = json.loads(mock_kafka.produce.call_args[1]["value"])
        assert "correlation_id" in produced_value
        assert produced_value["correlation_id"] == cid


# ---------------------------------------------------------------------------
# Dead-letter envelope structure
# ---------------------------------------------------------------------------

class TestDeadLetterEnvelope:
    def test_envelope_has_required_base_fields(self):
        producer, mock_kafka = _make_producer()
        producer.publish("mq-telemetry-raw", {})
        dl_value = json.loads(mock_kafka.produce.call_args[1]["value"])
        assert "event_id" in dl_value
        assert "correlation_id" in dl_value
        assert "timestamp" in dl_value
        assert "event_type" in dl_value
        assert "payload" in dl_value

    def test_envelope_preserves_original_correlation_id(self):
        producer, mock_kafka = _make_producer()
        cid = str(uuid.uuid4())
        bad_event = {"correlation_id": cid}
        producer.publish("mq-telemetry-raw", bad_event)
        dl_value = json.loads(mock_kafka.produce.call_args[1]["value"])
        assert dl_value["correlation_id"] == cid


# ---------------------------------------------------------------------------
# flush
# ---------------------------------------------------------------------------

class TestFlush:
    def test_flush_delegates_to_kafka(self):
        producer, mock_kafka = _make_producer()
        producer.flush(5.0)
        mock_kafka.flush.assert_called_once_with(5.0)
