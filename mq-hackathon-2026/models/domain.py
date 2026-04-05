"""Core Pydantic domain models and enums for the MQ Guardian Platform.

Defines all shared data structures used across platform components:
- MQ topology objects (QueueManager, Queue, Channel, Application)
- Event models (TopologySnapshot, Anomaly, Drift, Approval, Audit)
- Intelligence models (DecisionReport, ComplexityMetric, CapacityAlert)
- Sandbox models (SandboxSession)
- Enums for type safety across the platform
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class QueueType(str, Enum):
    """Type of IBM MQ queue."""
    LOCAL = "local"
    REMOTE = "remote"
    TRANSMISSION = "transmission"


class ChannelDirection(str, Enum):
    """Direction of an MQ channel in a sender/receiver pair."""
    SENDER = "sender"
    RECEIVER = "receiver"


class AnomalySeverity(str, Enum):
    """Severity classification for detected anomalies (Req 6.4)."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFORMATIONAL = "informational"


class RiskScore(str, Enum):
    """Risk classification for topology change proposals (Req 22.2)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftType(str, Enum):
    """Classification of topology drift from target state (Req 6.6 / 20.3)."""
    CONFIGURATION = "configuration_drift"
    STRUCTURAL = "structural_drift"
    POLICY = "policy_drift"


class ApprovalDecision(str, Enum):
    """Human-in-the-Loop decision types (Req 7.1-7.7)."""
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    DEFER = "defer"
    ESCALATE = "escalate"
    BATCH_APPROVE = "batch_approve"


class OperationalMode(str, Enum):
    """Platform operational mode (Req 14.5)."""
    BOOTSTRAP = "bootstrap"
    STEADY_STATE = "steady_state"


# ---------------------------------------------------------------------------
# Core MQ Objects
# ---------------------------------------------------------------------------

class QueueManager(BaseModel):
    """An IBM MQ queue manager that hosts queues and channels (Req 1.5, 2.2)."""
    id: str
    name: str
    hostname: str
    port: int
    neighborhood: Optional[str] = None
    region: Optional[str] = None
    lob: Optional[str] = None
    max_queues: int = 5000
    max_channels: int = 1000
    metadata: dict = Field(default_factory=dict)


class Queue(BaseModel):
    """An IBM MQ queue (local, remote, or transmission) hosted on a QM (Req 1.5, 2.2)."""
    id: str
    name: str
    queue_type: QueueType
    owning_qm_id: str
    target_qm_id: Optional[str] = None  # for remote queues
    xmitq_id: Optional[str] = None      # for remote queues
    lob: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class Channel(BaseModel):
    """An MQ channel connecting two queue managers (Req 1.5, 2.2).

    Channel names follow deterministic naming: fromQM.toQM / toQM.fromQM.
    """
    id: str
    name: str
    direction: ChannelDirection
    from_qm_id: str
    to_qm_id: str
    lob: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class Application(BaseModel):
    """A software application that produces or consumes messages via IBM MQ (Req 1.5, 2.2).

    Each application connects to exactly one queue manager.
    """
    id: str
    name: str
    connected_qm_id: str
    role: Literal["producer", "consumer", "both"]
    produces_to: list[str] = Field(default_factory=list)
    consumes_from: list[str] = Field(default_factory=list)
    criticality: Optional[str] = None
    lob: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Event Models
# ---------------------------------------------------------------------------

class TopologySnapshotEvent(BaseModel):
    """A point-in-time topology snapshot published to the event bus (Req 1.5, 2.2)."""
    snapshot_id: str
    correlation_id: str
    timestamp: datetime
    mode: OperationalMode
    queue_managers: list[QueueManager]
    queues: list[Queue]
    channels: list[Channel]
    applications: list[Application]


class AnomalyEvent(BaseModel):
    """A detected topology anomaly published to mq-anomaly-detected (Req 6.2)."""
    anomaly_id: str
    correlation_id: str
    timestamp: datetime
    anomaly_type: str
    severity: AnomalySeverity
    affected_objects: list[str]
    description: str
    recommended_remediation: str


class DriftEvent(BaseModel):
    """A detected drift from target-state topology (Req 6.6)."""
    drift_id: str
    correlation_id: str
    timestamp: datetime
    drift_type: DriftType
    affected_objects: list[str]
    delta_description: str
    proposed_remediation: str


class ApprovalRequest(BaseModel):
    """An HiTL approval request published to mq-approval-needed (Req 7.1, 7.12)."""
    approval_id: str
    correlation_id: str
    timestamp: datetime
    change_description: str
    blast_radius: dict
    risk_score: RiskScore
    decision_report: dict
    rollback_plan: dict
    requires_multi_level: bool = False
    affected_lobs: list[str] = Field(default_factory=list)


class ApprovalResponse(BaseModel):
    """A human approval decision published to mq-human-feedback (Req 7.1-7.7)."""
    approval_id: str
    correlation_id: str
    timestamp: datetime
    decision: ApprovalDecision
    actor: str
    modified_params: Optional[dict] = None
    rejection_reason: Optional[str] = None
    defer_reminder: Optional[datetime] = None
    batch_approval_ids: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Intelligence & Reporting Models
# ---------------------------------------------------------------------------

class DecisionReport(BaseModel):
    """Structured explanation for a topology change proposal (Req 21.7)."""
    report_id: str
    correlation_id: str
    summary: str
    reasoning_chain: list[str]
    policy_references: list[dict]
    alternatives_considered: list[dict]
    risk_assessment: str
    recommendation: str


class AuditEvent(BaseModel):
    """An immutable audit record persisted to the Audit_Store (Req 12.2)."""
    event_id: str
    correlation_id: str
    timestamp: datetime
    actor: str
    action_type: str
    affected_objects: list[str]
    outcome: str
    details: dict = Field(default_factory=dict)


class ComplexityMetricModel(BaseModel):
    """Quantitative complexity score for a topology snapshot (Req 5.1)."""
    total_channels: int
    avg_routing_hops: float
    max_fan_in: int
    max_fan_out: int
    avg_fan_in: float
    avg_fan_out: float
    redundant_objects: int
    composite_score: float


class CapacityAlert(BaseModel):
    """A predictive capacity alert from Prophet ML forecasting (Req 22.6)."""
    alert_id: str
    correlation_id: str
    affected_qm: str
    predicted_breach_date: datetime
    current_utilization: float
    threshold: float
    recommended_action: str


# ---------------------------------------------------------------------------
# Sandbox Models
# ---------------------------------------------------------------------------

class SandboxSession(BaseModel):
    """An isolated change simulation session (Req 14.5)."""
    session_id: str
    operator_id: str
    created_at: datetime
    topology_copy: Optional[dict] = None
    applied_changes: list[dict] = Field(default_factory=list)
    cumulative_complexity_delta: Optional[dict] = None
