"""Unit tests for core domain models and enums (models/domain.py).

Validates: Requirements 1.5, 14.5
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from models.domain import (
    AnomalyEvent,
    AnomalySeverity,
    Application,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AuditEvent,
    CapacityAlert,
    Channel,
    ChannelDirection,
    ComplexityMetricModel,
    DecisionReport,
    DriftEvent,
    DriftType,
    OperationalMode,
    Queue,
    QueueManager,
    QueueType,
    RiskScore,
    SandboxSession,
    TopologySnapshotEvent,
)


# ---------------------------------------------------------------------------
# Enum Tests
# ---------------------------------------------------------------------------


class TestQueueType:
    def test_values(self):
        assert QueueType.LOCAL == "local"
        assert QueueType.REMOTE == "remote"
        assert QueueType.TRANSMISSION == "transmission"

    def test_member_count(self):
        assert len(QueueType) == 3


class TestChannelDirection:
    def test_values(self):
        assert ChannelDirection.SENDER == "sender"
        assert ChannelDirection.RECEIVER == "receiver"

    def test_member_count(self):
        assert len(ChannelDirection) == 2


class TestAnomalySeverity:
    def test_values(self):
        assert AnomalySeverity.CRITICAL == "critical"
        assert AnomalySeverity.WARNING == "warning"
        assert AnomalySeverity.INFORMATIONAL == "informational"


class TestRiskScore:
    def test_values(self):
        assert RiskScore.LOW == "low"
        assert RiskScore.MEDIUM == "medium"
        assert RiskScore.HIGH == "high"
        assert RiskScore.CRITICAL == "critical"

    def test_member_count(self):
        assert len(RiskScore) == 4


class TestDriftType:
    def test_values(self):
        assert DriftType.CONFIGURATION == "configuration_drift"
        assert DriftType.STRUCTURAL == "structural_drift"
        assert DriftType.POLICY == "policy_drift"


class TestApprovalDecision:
    def test_all_six_decisions(self):
        assert ApprovalDecision.APPROVE == "approve"
        assert ApprovalDecision.REJECT == "reject"
        assert ApprovalDecision.MODIFY == "modify"
        assert ApprovalDecision.DEFER == "defer"
        assert ApprovalDecision.ESCALATE == "escalate"
        assert ApprovalDecision.BATCH_APPROVE == "batch_approve"

    def test_member_count(self):
        assert len(ApprovalDecision) == 6


class TestOperationalMode:
    def test_values(self):
        assert OperationalMode.BOOTSTRAP == "bootstrap"
        assert OperationalMode.STEADY_STATE == "steady_state"


# ---------------------------------------------------------------------------
# Core MQ Object Tests
# ---------------------------------------------------------------------------


class TestQueueManager:
    def test_create_minimal(self):
        qm = QueueManager(id="qm1", name="QM.ONE", hostname="host1", port=1414)
        assert qm.id == "qm1"
        assert qm.port == 1414
        assert qm.neighborhood is None
        assert qm.region is None
        assert qm.lob is None
        assert qm.max_queues == 5000
        assert qm.max_channels == 1000
        assert qm.metadata == {}

    def test_create_full(self):
        qm = QueueManager(
            id="qm2",
            name="QM.TWO",
            hostname="host2",
            port=1415,
            neighborhood="east",
            region="us-east-1",
            lob="retail",
            max_queues=3000,
            max_channels=500,
            metadata={"env": "prod"},
        )
        assert qm.lob == "retail"
        assert qm.metadata["env"] == "prod"

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            QueueManager(id="qm1", name="QM.ONE", hostname="host1")  # missing port

    def test_serialization_roundtrip(self):
        qm = QueueManager(id="qm1", name="QM.ONE", hostname="host1", port=1414)
        data = qm.model_dump()
        restored = QueueManager(**data)
        assert restored == qm

    def test_json_roundtrip(self):
        qm = QueueManager(id="qm1", name="QM.ONE", hostname="host1", port=1414)
        json_str = qm.model_dump_json()
        restored = QueueManager.model_validate_json(json_str)
        assert restored == qm


class TestQueue:
    def test_local_queue(self):
        q = Queue(id="q1", name="QL.APP1", queue_type=QueueType.LOCAL, owning_qm_id="qm1")
        assert q.queue_type == QueueType.LOCAL
        assert q.target_qm_id is None
        assert q.xmitq_id is None

    def test_remote_queue(self):
        q = Queue(
            id="q2",
            name="QR.APP1.TO.QM2",
            queue_type=QueueType.REMOTE,
            owning_qm_id="qm1",
            target_qm_id="qm2",
            xmitq_id="xq1",
        )
        assert q.target_qm_id == "qm2"
        assert q.xmitq_id == "xq1"

    def test_invalid_queue_type(self):
        with pytest.raises(ValidationError):
            Queue(id="q1", name="Q", queue_type="invalid", owning_qm_id="qm1")

    def test_serialization_roundtrip(self):
        q = Queue(id="q1", name="QL.APP1", queue_type=QueueType.LOCAL, owning_qm_id="qm1")
        restored = Queue(**q.model_dump())
        assert restored == q


class TestChannel:
    def test_create(self):
        ch = Channel(
            id="ch1",
            name="QM1.QM2",
            direction=ChannelDirection.SENDER,
            from_qm_id="qm1",
            to_qm_id="qm2",
        )
        assert ch.direction == ChannelDirection.SENDER
        assert ch.lob is None

    def test_invalid_direction(self):
        with pytest.raises(ValidationError):
            Channel(
                id="ch1", name="QM1.QM2", direction="bidirectional",
                from_qm_id="qm1", to_qm_id="qm2",
            )

    def test_serialization_roundtrip(self):
        ch = Channel(
            id="ch1", name="QM1.QM2", direction=ChannelDirection.SENDER,
            from_qm_id="qm1", to_qm_id="qm2",
        )
        restored = Channel(**ch.model_dump())
        assert restored == ch


class TestApplication:
    def test_producer(self):
        app = Application(
            id="app1", name="OrderService", connected_qm_id="qm1",
            role="producer", produces_to=["q1", "q2"],
        )
        assert app.role == "producer"
        assert len(app.produces_to) == 2
        assert app.consumes_from == []

    def test_consumer(self):
        app = Application(
            id="app2", name="NotifService", connected_qm_id="qm2",
            role="consumer", consumes_from=["q3"],
        )
        assert app.role == "consumer"

    def test_both_role(self):
        app = Application(
            id="app3", name="GatewayService", connected_qm_id="qm1",
            role="both", produces_to=["q1"], consumes_from=["q2"],
        )
        assert app.role == "both"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            Application(
                id="app1", name="App", connected_qm_id="qm1", role="observer",
            )

    def test_optional_fields_default(self):
        app = Application(id="a1", name="A", connected_qm_id="qm1", role="producer")
        assert app.criticality is None
        assert app.lob is None
        assert app.metadata == {}


# ---------------------------------------------------------------------------
# Event Model Tests
# ---------------------------------------------------------------------------

NOW = datetime(2025, 1, 15, 12, 0, 0)


class TestTopologySnapshotEvent:
    def test_create_empty_snapshot(self):
        snap = TopologySnapshotEvent(
            snapshot_id="s1", correlation_id="c1", timestamp=NOW,
            mode=OperationalMode.BOOTSTRAP,
            queue_managers=[], queues=[], channels=[], applications=[],
        )
        assert snap.mode == OperationalMode.BOOTSTRAP
        assert snap.queue_managers == []

    def test_create_with_objects(self):
        qm = QueueManager(id="qm1", name="QM1", hostname="h1", port=1414)
        q = Queue(id="q1", name="QL1", queue_type=QueueType.LOCAL, owning_qm_id="qm1")
        snap = TopologySnapshotEvent(
            snapshot_id="s1", correlation_id="c1", timestamp=NOW,
            mode=OperationalMode.STEADY_STATE,
            queue_managers=[qm], queues=[q], channels=[], applications=[],
        )
        assert len(snap.queue_managers) == 1
        assert snap.queues[0].name == "QL1"

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            TopologySnapshotEvent(
                snapshot_id="s1", correlation_id="c1", timestamp=NOW,
                mode=OperationalMode.BOOTSTRAP,
                # missing queues, channels, applications
                queue_managers=[],
            )


class TestAnomalyEvent:
    def test_create(self):
        evt = AnomalyEvent(
            anomaly_id="a1", correlation_id="c1", timestamp=NOW,
            anomaly_type="orphaned_queue", severity=AnomalySeverity.WARNING,
            affected_objects=["q1", "q2"],
            description="Orphaned queues detected",
            recommended_remediation="Remove orphaned queues",
        )
        assert evt.severity == AnomalySeverity.WARNING
        assert len(evt.affected_objects) == 2

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            AnomalyEvent(
                anomaly_id="a1", correlation_id="c1", timestamp=NOW,
                anomaly_type="test", severity="unknown",
                affected_objects=[], description="d", recommended_remediation="r",
            )


class TestDriftEvent:
    def test_create(self):
        evt = DriftEvent(
            drift_id="d1", correlation_id="c1", timestamp=NOW,
            drift_type=DriftType.CONFIGURATION,
            affected_objects=["qm1"],
            delta_description="Port changed",
            proposed_remediation="Revert port",
        )
        assert evt.drift_type == DriftType.CONFIGURATION

    def test_invalid_drift_type(self):
        with pytest.raises(ValidationError):
            DriftEvent(
                drift_id="d1", correlation_id="c1", timestamp=NOW,
                drift_type="unknown_drift",
                affected_objects=[], delta_description="d", proposed_remediation="r",
            )


class TestApprovalRequest:
    def test_create_minimal(self):
        req = ApprovalRequest(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            change_description="Add queue", blast_radius={"qms": 1},
            risk_score=RiskScore.LOW, decision_report={"summary": "ok"},
            rollback_plan={"steps": []},
        )
        assert req.requires_multi_level is False
        assert req.affected_lobs == []

    def test_high_risk_multi_level(self):
        req = ApprovalRequest(
            approval_id="ap2", correlation_id="c2", timestamp=NOW,
            change_description="Major restructure",
            blast_radius={"qms": 15}, risk_score=RiskScore.CRITICAL,
            decision_report={}, rollback_plan={},
            requires_multi_level=True, affected_lobs=["retail", "wholesale"],
        )
        assert req.requires_multi_level is True
        assert len(req.affected_lobs) == 2

    def test_invalid_risk_score(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(
                approval_id="ap1", correlation_id="c1", timestamp=NOW,
                change_description="x", blast_radius={},
                risk_score="extreme", decision_report={}, rollback_plan={},
            )


class TestApprovalResponse:
    def test_approve(self):
        resp = ApprovalResponse(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            decision=ApprovalDecision.APPROVE, actor="operator1",
        )
        assert resp.decision == ApprovalDecision.APPROVE
        assert resp.modified_params is None
        assert resp.rejection_reason is None

    def test_reject_with_reason(self):
        resp = ApprovalResponse(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            decision=ApprovalDecision.REJECT, actor="operator1",
            rejection_reason="Too risky",
        )
        assert resp.rejection_reason == "Too risky"

    def test_modify_with_params(self):
        resp = ApprovalResponse(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            decision=ApprovalDecision.MODIFY, actor="operator1",
            modified_params={"port": 1415},
        )
        assert resp.modified_params["port"] == 1415

    def test_defer_with_reminder(self):
        reminder = datetime(2025, 1, 16, 9, 0, 0)
        resp = ApprovalResponse(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            decision=ApprovalDecision.DEFER, actor="operator1",
            defer_reminder=reminder,
        )
        assert resp.defer_reminder == reminder

    def test_batch_approve(self):
        resp = ApprovalResponse(
            approval_id="ap1", correlation_id="c1", timestamp=NOW,
            decision=ApprovalDecision.BATCH_APPROVE, actor="operator1",
            batch_approval_ids=["ap2", "ap3"],
        )
        assert len(resp.batch_approval_ids) == 2


# ---------------------------------------------------------------------------
# Intelligence & Reporting Model Tests
# ---------------------------------------------------------------------------


class TestDecisionReport:
    def test_create(self):
        report = DecisionReport(
            report_id="r1", correlation_id="c1",
            summary="Consolidate QMs",
            reasoning_chain=["Step 1", "Step 2"],
            policy_references=[{"policy_id": "p1", "description": "1 QM per app"}],
            alternatives_considered=[{"action": "Do nothing", "rejection_reason": "Drift persists"}],
            risk_assessment="low",
            recommendation="Proceed with consolidation",
        )
        assert len(report.reasoning_chain) == 2
        assert len(report.policy_references) == 1

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            DecisionReport(
                report_id="r1", correlation_id="c1",
                summary="x",
                # missing reasoning_chain, etc.
            )


class TestAuditEvent:
    def test_create(self):
        evt = AuditEvent(
            event_id="e1", correlation_id="c1", timestamp=NOW,
            actor="agent_brain", action_type="topology_ingestion",
            affected_objects=["qm1"], outcome="success",
        )
        assert evt.details == {}

    def test_with_details(self):
        evt = AuditEvent(
            event_id="e1", correlation_id="c1", timestamp=NOW,
            actor="operator1", action_type="approval_decision",
            affected_objects=["qm1", "q1"], outcome="approved",
            details={"decision": "approve", "approval_id": "ap1"},
        )
        assert evt.details["decision"] == "approve"


class TestComplexityMetricModel:
    def test_create(self):
        m = ComplexityMetricModel(
            total_channels=50, avg_routing_hops=2.3,
            max_fan_in=8, max_fan_out=10,
            avg_fan_in=3.2, avg_fan_out=3.5,
            redundant_objects=5, composite_score=42.7,
        )
        assert m.total_channels == 50
        assert m.composite_score == 42.7

    def test_missing_field(self):
        with pytest.raises(ValidationError):
            ComplexityMetricModel(
                total_channels=50, avg_routing_hops=2.3,
                # missing remaining fields
            )


class TestCapacityAlert:
    def test_create(self):
        alert = CapacityAlert(
            alert_id="ca1", correlation_id="c1",
            affected_qm="qm1",
            predicted_breach_date=datetime(2025, 3, 1),
            current_utilization=0.85, threshold=0.9,
            recommended_action="Add capacity",
        )
        assert alert.current_utilization == 0.85


# ---------------------------------------------------------------------------
# Sandbox Model Tests
# ---------------------------------------------------------------------------


class TestSandboxSession:
    def test_create_minimal(self):
        session = SandboxSession(
            session_id="ss1", operator_id="op1", created_at=NOW,
        )
        assert session.topology_copy is None
        assert session.applied_changes == []
        assert session.cumulative_complexity_delta is None

    def test_create_with_data(self):
        session = SandboxSession(
            session_id="ss1", operator_id="op1", created_at=NOW,
            topology_copy={"qms": ["qm1"]},
            applied_changes=[{"action": "add_queue", "queue_id": "q1"}],
            cumulative_complexity_delta={"channels": -2},
        )
        assert len(session.applied_changes) == 1
        assert session.cumulative_complexity_delta["channels"] == -2

    def test_serialization_roundtrip(self):
        session = SandboxSession(
            session_id="ss1", operator_id="op1", created_at=NOW,
            applied_changes=[{"action": "remove_channel"}],
        )
        data = session.model_dump()
        restored = SandboxSession(**data)
        assert restored == session
