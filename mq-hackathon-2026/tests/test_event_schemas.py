"""Tests for event_bus.schemas — topics, JSON schemas, and validate_message."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from event_bus.schemas import (
    APPROVAL_REQUEST_SCHEMA,
    BASE_EVENT_SCHEMA,
    DECISION_REPORT_SCHEMA,
    HUMAN_FEEDBACK_SCHEMA,
    KAFKA_TOPICS,
    TOPIC_SCHEMAS,
    validate_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_event(**overrides) -> dict:
    """Return a minimal valid BASE_EVENT_SCHEMA message."""
    msg = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "test_event",
        "payload": {},
    }
    msg.update(overrides)
    return msg


def _approval_request(**payload_overrides) -> dict:
    """Return a minimal valid APPROVAL_REQUEST_SCHEMA message."""
    payload = {
        "approval_id": str(uuid.uuid4()),
        "change_description": "Add new queue manager QM42",
        "blast_radius": {"affected_qms": 2},
        "risk_score": "medium",
        "decision_report": {"summary": "ok"},
        "rollback_plan": {"steps": []},
    }
    payload.update(payload_overrides)
    return _base_event(payload=payload)


def _human_feedback(**overrides) -> dict:
    """Return a minimal valid HUMAN_FEEDBACK_SCHEMA message."""
    msg = {
        "approval_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "decision": "approve",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    msg.update(overrides)
    return msg


# ---------------------------------------------------------------------------
# Topic registry
# ---------------------------------------------------------------------------

class TestKafkaTopics:
    def test_has_exactly_nine_topics(self):
        assert len(KAFKA_TOPICS) == 9

    def test_all_expected_topics_present(self):
        expected = {
            "mq-telemetry-raw",
            "mq-topology-snapshot",
            "mq-anomaly-detected",
            "mq-provision-request",
            "mq-approval-needed",
            "mq-human-feedback",
            "mq-audit-log",
            "mq-predictive-alert",
            "mq-dead-letter",
        }
        assert set(KAFKA_TOPICS.keys()) == expected

    def test_every_topic_has_description(self):
        for topic, desc in KAFKA_TOPICS.items():
            assert isinstance(desc, str) and len(desc) > 0, f"{topic} missing description"

    def test_topic_schema_mapping_covers_all_topics(self):
        assert set(TOPIC_SCHEMAS.keys()) == set(KAFKA_TOPICS.keys())


# ---------------------------------------------------------------------------
# Schema structure
# ---------------------------------------------------------------------------

class TestSchemaStructure:
    def test_base_event_schema_requires_correlation_id(self):
        assert "correlation_id" in BASE_EVENT_SCHEMA["required"]

    def test_approval_request_schema_requires_payload_fields(self):
        payload_schema = APPROVAL_REQUEST_SCHEMA["properties"]["payload"]
        assert "approval_id" in payload_schema["required"]
        assert "risk_score" in payload_schema["required"]

    def test_human_feedback_schema_requires_decision(self):
        assert "decision" in HUMAN_FEEDBACK_SCHEMA["required"]

    def test_human_feedback_decision_enum(self):
        enum_vals = HUMAN_FEEDBACK_SCHEMA["properties"]["decision"]["enum"]
        assert set(enum_vals) == {
            "approve", "reject", "modify", "defer", "escalate", "batch_approve",
        }

    def test_decision_report_schema_required_fields(self):
        assert "summary" in DECISION_REPORT_SCHEMA["required"]
        assert "reasoning_chain" in DECISION_REPORT_SCHEMA["required"]
        assert "recommendation" in DECISION_REPORT_SCHEMA["required"]


# ---------------------------------------------------------------------------
# validate_message — valid messages
# ---------------------------------------------------------------------------

class TestValidateMessageValid:
    def test_base_event_valid(self):
        ok, errors = validate_message("mq-telemetry-raw", _base_event())
        assert ok is True
        assert errors == []

    def test_approval_request_valid(self):
        ok, errors = validate_message("mq-approval-needed", _approval_request())
        assert ok is True
        assert errors == []

    def test_human_feedback_valid(self):
        ok, errors = validate_message("mq-human-feedback", _human_feedback())
        assert ok is True
        assert errors == []

    @pytest.mark.parametrize("topic", [
        "mq-topology-snapshot",
        "mq-anomaly-detected",
        "mq-provision-request",
        "mq-audit-log",
        "mq-predictive-alert",
        "mq-dead-letter",
    ])
    def test_base_event_topics_accept_valid_message(self, topic):
        ok, errors = validate_message(topic, _base_event())
        assert ok is True

    @pytest.mark.parametrize("decision", [
        "approve", "reject", "modify", "defer", "escalate", "batch_approve",
    ])
    def test_human_feedback_all_decision_types_valid(self, decision):
        """Req 13.2: schema accepts every defined decision enum value."""
        ok, errors = validate_message("mq-human-feedback", _human_feedback(decision=decision))
        assert ok is True
        assert errors == []

    @pytest.mark.parametrize("risk", ["low", "medium", "high", "critical"])
    def test_approval_request_all_risk_scores_valid(self, risk):
        """Req 13.2: schema accepts every defined risk_score enum value."""
        ok, errors = validate_message("mq-approval-needed", _approval_request(risk_score=risk))
        assert ok is True
        assert errors == []


# ---------------------------------------------------------------------------
# validate_message — invalid messages
# ---------------------------------------------------------------------------

class TestValidateMessageInvalid:
    def test_unknown_topic(self):
        ok, errors = validate_message("nonexistent-topic", {})
        assert ok is False
        assert any("Unknown topic" in e for e in errors)

    def test_missing_required_fields(self):
        ok, errors = validate_message("mq-telemetry-raw", {})
        assert ok is False
        assert len(errors) > 0

    def test_missing_correlation_id(self):
        msg = _base_event()
        del msg["correlation_id"]
        ok, errors = validate_message("mq-telemetry-raw", msg)
        assert ok is False

    def test_approval_request_bad_risk_score(self):
        msg = _approval_request(risk_score="extreme")
        ok, errors = validate_message("mq-approval-needed", msg)
        assert ok is False

    def test_human_feedback_bad_decision(self):
        msg = _human_feedback(decision="maybe")
        ok, errors = validate_message("mq-human-feedback", msg)
        assert ok is False

    def test_human_feedback_missing_decision(self):
        msg = _human_feedback()
        del msg["decision"]
        ok, errors = validate_message("mq-human-feedback", msg)
        assert ok is False

    def test_error_messages_are_descriptive_strings(self):
        """Req 13.3: validation errors are human-readable strings."""
        ok, errors = validate_message("mq-telemetry-raw", {})
        assert ok is False
        assert all(isinstance(e, str) for e in errors)
        assert any("required" in e.lower() for e in errors)

    def test_approval_request_missing_payload_field(self):
        """Req 13.2: approval schema rejects payload missing required fields."""
        msg = _approval_request()
        del msg["payload"]["rollback_plan"]
        ok, errors = validate_message("mq-approval-needed", msg)
        assert ok is False
        assert len(errors) > 0
