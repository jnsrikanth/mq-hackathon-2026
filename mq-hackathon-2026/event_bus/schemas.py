"""Kafka event schemas and validation utilities for the MQ Guardian Platform.

Defines all 9 Kafka topics, JSON schemas for structured messages, and a
``validate_message`` helper that validates any message against the schema
associated with its topic.

Requirements: 13.1, 13.2, 13.3, 13.5
"""

from __future__ import annotations

from typing import Any

import jsonschema

# ---------------------------------------------------------------------------
# Kafka Topics (Req 13.1)
# ---------------------------------------------------------------------------

KAFKA_TOPICS: dict[str, str] = {
    "mq-telemetry-raw": "Live MQ telemetry data",
    "mq-topology-snapshot": "Complete topology snapshots",
    "mq-anomaly-detected": "Detected anomalies and drift events",
    "mq-provision-request": "Provisioning requests from Chatbot",
    "mq-approval-needed": "HiTL approval requests",
    "mq-human-feedback": "Human approval/rejection decisions",
    "mq-audit-log": "Full audit trail",
    "mq-predictive-alert": "Prophet ML forecasting alerts",
    "mq-dead-letter": "Failed messages after retry exhaustion",
}

# ---------------------------------------------------------------------------
# JSON Schemas (Req 13.2, 13.5)
# ---------------------------------------------------------------------------

BASE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["event_id", "correlation_id", "timestamp", "event_type", "payload"],
    "properties": {
        "event_id": {"type": "string", "format": "uuid"},
        "correlation_id": {"type": "string", "format": "uuid"},
        "timestamp": {"type": "string", "format": "date-time"},
        "event_type": {"type": "string"},
        "payload": {"type": "object"},
    },
}

APPROVAL_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["event_id", "correlation_id", "timestamp", "event_type", "payload"],
    "properties": {
        **BASE_EVENT_SCHEMA["properties"],
        "payload": {
            "type": "object",
            "required": [
                "approval_id",
                "change_description",
                "blast_radius",
                "risk_score",
                "decision_report",
                "rollback_plan",
            ],
            "properties": {
                "approval_id": {"type": "string", "format": "uuid"},
                "change_description": {"type": "string"},
                "blast_radius": {"type": "object"},
                "risk_score": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "decision_report": {"type": "object"},
                "rollback_plan": {"type": "object"},
            },
        },
    },
}

HUMAN_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["approval_id", "correlation_id", "decision", "timestamp"],
    "properties": {
        "approval_id": {"type": "string"},
        "correlation_id": {"type": "string"},
        "decision": {
            "type": "string",
            "enum": ["approve", "reject", "modify", "defer", "escalate", "batch_approve"],
        },
        "modified_params": {"type": "object"},
        "rejection_reason": {"type": "string"},
        "defer_reminder_timestamp": {"type": "string", "format": "date-time"},
        "batch_approval_ids": {"type": "array", "items": {"type": "string"}},
        "timestamp": {"type": "string", "format": "date-time"},
    },
}

DECISION_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "reasoning_chain",
        "policy_references",
        "alternatives_considered",
        "risk_assessment",
        "recommendation",
    ],
    "properties": {
        "summary": {"type": "string"},
        "reasoning_chain": {"type": "array", "items": {"type": "string"}},
        "policy_references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "policy_id": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "alternatives_considered": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rejection_reason": {"type": "string"},
                },
            },
        },
        "risk_assessment": {"type": "string"},
        "recommendation": {"type": "string"},
    },
}

# ---------------------------------------------------------------------------
# Topic → Schema mapping
# ---------------------------------------------------------------------------

TOPIC_SCHEMAS: dict[str, dict[str, Any]] = {
    "mq-telemetry-raw": BASE_EVENT_SCHEMA,
    "mq-topology-snapshot": BASE_EVENT_SCHEMA,
    "mq-anomaly-detected": BASE_EVENT_SCHEMA,
    "mq-provision-request": BASE_EVENT_SCHEMA,
    "mq-approval-needed": APPROVAL_REQUEST_SCHEMA,
    "mq-human-feedback": HUMAN_FEEDBACK_SCHEMA,
    "mq-audit-log": BASE_EVENT_SCHEMA,
    "mq-predictive-alert": BASE_EVENT_SCHEMA,
    "mq-dead-letter": BASE_EVENT_SCHEMA,
}

# ---------------------------------------------------------------------------
# Validation utility (Req 13.2, 13.3)
# ---------------------------------------------------------------------------


def validate_message(topic: str, message: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate *message* against the JSON schema registered for *topic*.

    Parameters
    ----------
    topic:
        Kafka topic name (must be one of :data:`KAFKA_TOPICS`).
    message:
        The message dict to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` when the message is valid, or
        ``(False, errors)`` with a list of human-readable error strings.
    """
    if topic not in KAFKA_TOPICS:
        return False, [f"Unknown topic: {topic}"]

    schema = TOPIC_SCHEMAS.get(topic, BASE_EVENT_SCHEMA)

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(message), key=lambda e: list(e.absolute_path))

    if not errors:
        return True, []

    error_messages = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]
    return False, error_messages
