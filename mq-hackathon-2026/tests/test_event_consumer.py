"""Tests for event_bus.consumer — EventBusConsumer wrapper."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from event_bus.consumer import DEFAULT_CONSUMER_GROUP, EventBusConsumer, InMemoryConsumer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_consumer(group_id: str = DEFAULT_CONSUMER_GROUP) -> tuple[EventBusConsumer, MagicMock]:
    mock_kafka = MagicMock()
    consumer = EventBusConsumer(consumer=mock_kafka, group_id=group_id)
    return consumer, mock_kafka


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


def _make_kafka_message(topic: str, value: bytes | None, error=None) -> MagicMock:
    msg = MagicMock()
    msg.topic.return_value = topic
    msg.value.return_value = value
    msg.partition.return_value = 0
    msg.offset.return_value = 42
    msg.error.return_value = error
    return msg


# ---------------------------------------------------------------------------
# InMemoryConsumer
# ---------------------------------------------------------------------------

class TestInMemoryConsumer:
    def test_inject_and_poll(self):
        c = InMemoryConsumer()
        c.subscribe(["test-topic"])
        c.inject_message("test-topic", b'{"key": "val"}')
        msg = c.poll(0.1)
        assert msg is not None
        assert msg.topic() == "test-topic"
        assert msg.value() == b'{"key": "val"}'

    def test_poll_returns_none_when_empty(self):
        c = InMemoryConsumer()
        c.subscribe(["test-topic"])
        assert c.poll(0.01) is None

    def test_default_constructor_uses_in_memory(self):
        consumer = EventBusConsumer()
        assert isinstance(consumer._consumer, InMemoryConsumer)


# ---------------------------------------------------------------------------
# Constructor and properties
# ---------------------------------------------------------------------------

class TestConsumerInit:
    def test_default_group_id(self):
        consumer, _ = _make_consumer()
        assert consumer.group_id == DEFAULT_CONSUMER_GROUP

    def test_custom_group_id(self):
        consumer, _ = _make_consumer(group_id="my-custom-group")
        assert consumer.group_id == "my-custom-group"

    def test_subscribed_topics_initially_empty(self):
        consumer, _ = _make_consumer()
        assert consumer.subscribed_topics == []


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------

class TestSubscribe:
    def test_subscribe_valid_topics(self):
        consumer, mock_kafka = _make_consumer()
        consumer.subscribe(["mq-telemetry-raw", "mq-topology-snapshot"])
        mock_kafka.subscribe.assert_called_once()
        assert consumer.subscribed_topics == ["mq-telemetry-raw", "mq-topology-snapshot"]

    def test_subscribe_filters_unknown_topics(self):
        consumer, mock_kafka = _make_consumer()
        consumer.subscribe(["mq-telemetry-raw", "nonexistent-topic"])
        assert consumer.subscribed_topics == ["mq-telemetry-raw"]

    def test_subscribe_all_unknown_does_not_call_kafka(self):
        consumer, mock_kafka = _make_consumer()
        consumer.subscribe(["fake-topic-1", "fake-topic-2"])
        mock_kafka.subscribe.assert_not_called()
        assert consumer.subscribed_topics == []

    def test_subscribe_multi_topic_for_agent_brain(self):
        consumer, mock_kafka = _make_consumer()
        topics = ["mq-telemetry-raw", "mq-topology-snapshot", "mq-human-feedback"]
        consumer.subscribe(topics)
        assert consumer.subscribed_topics == topics

    def test_subscribe_passes_rebalance_callbacks(self):
        consumer, mock_kafka = _make_consumer()
        on_assign = MagicMock()
        on_revoke = MagicMock()
        consumer.subscribe(["mq-telemetry-raw"], on_assign=on_assign, on_revoke=on_revoke)
        _, kwargs = mock_kafka.subscribe.call_args
        assert kwargs["on_assign"] is on_assign
        assert kwargs["on_revoke"] is on_revoke


# ---------------------------------------------------------------------------
# poll_one — valid messages
# ---------------------------------------------------------------------------

class TestPollOneValid:
    def test_returns_deserialized_event(self):
        consumer, mock_kafka = _make_consumer()
        event = _base_event()
        kafka_msg = _make_kafka_message("mq-telemetry-raw", json.dumps(event).encode("utf-8"))
        mock_kafka.poll.return_value = kafka_msg
        result = consumer.poll_one(timeout=0.1)
        assert result is not None
        topic, deserialized, errors = result
        assert topic == "mq-telemetry-raw"
        assert deserialized == event
        assert errors == []

    def test_returns_none_on_timeout(self):
        consumer, mock_kafka = _make_consumer()
        mock_kafka.poll.return_value = None
        assert consumer.poll_one(timeout=0.1) is None


# ---------------------------------------------------------------------------
# poll_one — invalid messages
# ---------------------------------------------------------------------------

class TestPollOneInvalid:
    def test_invalid_json_returns_errors(self):
        consumer, mock_kafka = _make_consumer()
        kafka_msg = _make_kafka_message("mq-telemetry-raw", b"not-valid-json")
        mock_kafka.poll.return_value = kafka_msg
        result = consumer.poll_one(timeout=0.1)
        assert result is not None
        topic, event, errors = result
        assert topic == "mq-telemetry-raw"
        assert event == {}
        assert len(errors) == 1
        assert "JSON deserialization error" in errors[0]

    def test_schema_invalid_message_returns_errors(self):
        consumer, mock_kafka = _make_consumer()
        bad_event = {"not": "valid"}
        kafka_msg = _make_kafka_message("mq-telemetry-raw", json.dumps(bad_event).encode("utf-8"))
        mock_kafka.poll.return_value = kafka_msg
        result = consumer.poll_one(timeout=0.1)
        assert result is not None
        topic, event, errors = result
        assert event == bad_event
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# consume_loop
# ---------------------------------------------------------------------------

class TestConsumeLoop:
    def test_dispatches_messages_to_handler(self):
        consumer, mock_kafka = _make_consumer()
        event = _base_event()
        kafka_msg = _make_kafka_message("mq-audit-log", json.dumps(event).encode("utf-8"))
        mock_kafka.poll.side_effect = [kafka_msg, None]
        handler = MagicMock()
        count = consumer.consume_loop(handler, poll_timeout=0.01, max_messages=1)
        assert count == 1
        handler.assert_called_once()

    def test_max_messages_limits_consumption(self):
        consumer, mock_kafka = _make_consumer()
        events = [_base_event() for _ in range(5)]
        kafka_msgs = [
            _make_kafka_message("mq-audit-log", json.dumps(e).encode("utf-8"))
            for e in events
        ]
        mock_kafka.poll.side_effect = kafka_msgs
        handler = MagicMock()
        count = consumer.consume_loop(handler, poll_timeout=0.01, max_messages=3)
        assert count == 3


# ---------------------------------------------------------------------------
# commit and close
# ---------------------------------------------------------------------------

class TestCommitAndClose:
    def test_commit_delegates(self):
        consumer, mock_kafka = _make_consumer()
        consumer.commit(asynchronous=False)
        mock_kafka.commit.assert_called_once_with(asynchronous=False)

    def test_close_delegates(self):
        consumer, mock_kafka = _make_consumer()
        consumer.close()
        mock_kafka.close.assert_called_once()
