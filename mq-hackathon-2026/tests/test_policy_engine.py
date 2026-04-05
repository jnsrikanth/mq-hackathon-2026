"""Unit tests for the Policy Engine (Sentinel + OPA).

Covers MQ constraint validation (Req 3.1-3.6), Sentinel policies (Req 9.1),
OPA policies (Req 9.2), combined evaluation (Req 9.3), and compliance
attestation generation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    OperationalMode,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)
from policy_engine.engine import PolicyEngine, PolicyViolation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topology(
    queue_managers: list[QueueManager] | None = None,
    queues: list[Queue] | None = None,
    channels: list[Channel] | None = None,
    applications: list[Application] | None = None,
) -> TopologySnapshotEvent:
    """Build a minimal TopologySnapshotEvent for testing."""
    return TopologySnapshotEvent(
        snapshot_id="snap-1",
        correlation_id="corr-1",
        timestamp=datetime.now(timezone.utc),
        mode=OperationalMode.BOOTSTRAP,
        queue_managers=queue_managers or [],
        queues=queues or [],
        channels=channels or [],
        applications=applications or [],
    )


def _qm(id: str, name: str | None = None, **kwargs) -> QueueManager:
    return QueueManager(
        id=id, name=name or id, hostname="localhost", port=1414, **kwargs,
    )


def _queue(
    id: str, name: str, queue_type: QueueType, owning_qm_id: str, **kwargs,
) -> Queue:
    return Queue(
        id=id, name=name, queue_type=queue_type, owning_qm_id=owning_qm_id, **kwargs,
    )


def _channel(
    id: str, name: str, direction: ChannelDirection,
    from_qm_id: str, to_qm_id: str, **kwargs,
) -> Channel:
    return Channel(
        id=id, name=name, direction=direction,
        from_qm_id=from_qm_id, to_qm_id=to_qm_id, **kwargs,
    )


def _app(
    id: str, name: str, connected_qm_id: str, role: str,
    produces_to: list[str] | None = None,
    consumes_from: list[str] | None = None,
    **kwargs,
) -> Application:
    return Application(
        id=id, name=name, connected_qm_id=connected_qm_id, role=role,
        produces_to=produces_to or [],
        consumes_from=consumes_from or [],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# A valid "golden" topology used as a baseline for many tests
# ---------------------------------------------------------------------------

def _valid_topology() -> TopologySnapshotEvent:
    """Return a fully compliant topology with no violations."""
    qm_a = _qm("QM_A")
    qm_b = _qm("QM_B")

    # Remote queue on QM_A targeting QM_B
    rq = _queue("rq1", "RQ.A.TO.B", QueueType.REMOTE, "QM_A",
                 target_qm_id="QM_B", xmitq_id="xmitq1")
    # Transmission queue on QM_A
    xmitq = _queue("xmitq1", "XMITQ.A", QueueType.TRANSMISSION, "QM_A")
    # Local queue on QM_B
    lq = _queue("lq1", "LQ.B", QueueType.LOCAL, "QM_B")

    # Sender channel from QM_A to QM_B
    ch_sender = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B")

    # Producer on QM_A writes to remote queue
    producer = _app("app1", "ProducerApp", "QM_A", "producer", produces_to=["rq1"])
    # Consumer on QM_B reads from local queue
    consumer = _app("app2", "ConsumerApp", "QM_B", "consumer", consumes_from=["lq1"])

    return _make_topology(
        queue_managers=[qm_a, qm_b],
        queues=[rq, xmitq, lq],
        channels=[ch_sender],
        applications=[producer, consumer],
    )


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine()


# ===================================================================
# Req 3.1 — Exactly one QM per application
# ===================================================================

class TestExactlyOneQMPerApp:
    """Req 3.1: Each application connects to exactly one queue manager."""

    def test_valid_topology_no_violations(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.validate_mq_constraints(topo)
        qm_violations = [v for v in violations if v.policy_id == "exactly_one_qm_per_app"]
        assert qm_violations == []

    def test_app_with_no_qm(self, engine: PolicyEngine):
        app = _app("app1", "OrphanApp", "", "producer")
        topo = _make_topology(applications=[app])
        violations = engine.validate_mq_constraints(topo)
        qm_violations = [v for v in violations if v.policy_id == "exactly_one_qm_per_app"]
        assert len(qm_violations) >= 1
        assert "app1" in qm_violations[0].offending_objects

    def test_app_references_nonexistent_qm(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        app = _app("app1", "BadRefApp", "QM_MISSING", "consumer")
        topo = _make_topology(queue_managers=[qm], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        qm_violations = [v for v in violations if v.policy_id == "exactly_one_qm_per_app"]
        assert len(qm_violations) >= 1
        assert "QM_MISSING" in qm_violations[0].offending_objects


# ===================================================================
# Req 3.2 — Producers write to remote queue on own QM
# ===================================================================

class TestProducersWriteToRemoteQueueOnOwnQM:
    """Req 3.2: Producers must write to remote queues on their own QM."""

    def test_valid_producer(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.validate_mq_constraints(topo)
        prod_violations = [
            v for v in violations
            if v.policy_id == "producers_write_to_remote_queue_on_own_qm"
        ]
        assert prod_violations == []

    def test_producer_writes_to_local_queue(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        lq = _queue("lq1", "LQ.A", QueueType.LOCAL, "QM_A")
        app = _app("app1", "BadProducer", "QM_A", "producer", produces_to=["lq1"])
        topo = _make_topology(queue_managers=[qm], queues=[lq], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        prod_violations = [
            v for v in violations
            if v.policy_id == "producers_write_to_remote_queue_on_own_qm"
        ]
        assert len(prod_violations) >= 1
        assert any("local" in v.description for v in prod_violations)

    def test_producer_writes_to_queue_on_different_qm(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        rq = _queue("rq1", "RQ.B", QueueType.REMOTE, "QM_B", target_qm_id="QM_A", xmitq_id="x1")
        app = _app("app1", "CrossQMProducer", "QM_A", "producer", produces_to=["rq1"])
        topo = _make_topology(queue_managers=[qm_a, qm_b], queues=[rq], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        prod_violations = [
            v for v in violations
            if v.policy_id == "producers_write_to_remote_queue_on_own_qm"
        ]
        assert len(prod_violations) >= 1

    def test_producer_references_nonexistent_queue(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        app = _app("app1", "GhostProducer", "QM_A", "producer", produces_to=["missing_q"])
        topo = _make_topology(queue_managers=[qm], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        prod_violations = [
            v for v in violations
            if v.policy_id == "producers_write_to_remote_queue_on_own_qm"
        ]
        assert len(prod_violations) >= 1
        assert "missing_q" in prod_violations[0].offending_objects


# ===================================================================
# Req 3.3 — Remote queue uses xmitq via channel
# ===================================================================

class TestRemoteQueueUsesXmitqViaChannel:
    """Req 3.3: Remote queues must have xmitq and a channel to the target QM."""

    def test_valid_remote_queue(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.validate_mq_constraints(topo)
        rq_violations = [
            v for v in violations
            if v.policy_id == "remote_queue_uses_xmitq_via_channel"
        ]
        assert rq_violations == []

    def test_remote_queue_missing_xmitq(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        rq = _queue("rq1", "RQ.NO.XMITQ", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B")
        topo = _make_topology(queue_managers=[qm], queues=[rq])
        violations = engine.validate_mq_constraints(topo)
        rq_violations = [
            v for v in violations
            if v.policy_id == "remote_queue_uses_xmitq_via_channel"
        ]
        assert any("transmission queue" in v.description.lower() for v in rq_violations)

    def test_remote_queue_xmitq_references_nonexistent(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        rq = _queue("rq1", "RQ.BAD.XMITQ", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B", xmitq_id="ghost_xmitq")
        topo = _make_topology(queue_managers=[qm], queues=[rq])
        violations = engine.validate_mq_constraints(topo)
        rq_violations = [
            v for v in violations
            if v.policy_id == "remote_queue_uses_xmitq_via_channel"
        ]
        assert any("ghost_xmitq" in v.description for v in rq_violations)

    def test_remote_queue_xmitq_wrong_type(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        # xmitq is actually a local queue — wrong type
        bad_xmitq = _queue("xq1", "NOT.XMITQ", QueueType.LOCAL, "QM_A")
        rq = _queue("rq1", "RQ.WRONG.TYPE", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B", xmitq_id="xq1")
        topo = _make_topology(queue_managers=[qm], queues=[rq, bad_xmitq])
        violations = engine.validate_mq_constraints(topo)
        rq_violations = [
            v for v in violations
            if v.policy_id == "remote_queue_uses_xmitq_via_channel"
        ]
        assert any("transmission" in v.description.lower() for v in rq_violations)

    def test_remote_queue_no_channel_between_qms(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        xmitq = _queue("xq1", "XMITQ.A", QueueType.TRANSMISSION, "QM_A")
        rq = _queue("rq1", "RQ.A.TO.B", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B", xmitq_id="xq1")
        # No channel between QM_A and QM_B
        topo = _make_topology(queue_managers=[qm_a, qm_b], queues=[rq, xmitq])
        violations = engine.validate_mq_constraints(topo)
        rq_violations = [
            v for v in violations
            if v.policy_id == "remote_queue_uses_xmitq_via_channel"
        ]
        assert any("channel" in v.description.lower() for v in rq_violations)


# ===================================================================
# Req 3.4 — Deterministic channel naming
# ===================================================================

class TestDeterministicChannelNaming:
    """Req 3.4: Channels must follow fromQM.toQM naming convention."""

    def test_valid_channel_name(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.validate_mq_constraints(topo)
        name_violations = [
            v for v in violations if v.policy_id == "deterministic_channel_naming"
        ]
        assert name_violations == []

    def test_invalid_channel_name(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        ch = _channel("ch1", "WRONG_NAME", ChannelDirection.SENDER, "QM_A", "QM_B")
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        violations = engine.validate_mq_constraints(topo)
        name_violations = [
            v for v in violations if v.policy_id == "deterministic_channel_naming"
        ]
        assert len(name_violations) == 1
        assert "QM_A.QM_B" in name_violations[0].description

    def test_receiver_channel_naming(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        # Receiver on QM_B: from_qm_id=QM_B, to_qm_id=QM_A → expected "QM_B.QM_A"
        ch = _channel("ch1", "QM_B.QM_A", ChannelDirection.RECEIVER, "QM_B", "QM_A")
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        violations = engine.validate_mq_constraints(topo)
        name_violations = [
            v for v in violations if v.policy_id == "deterministic_channel_naming"
        ]
        assert name_violations == []


# ===================================================================
# Req 3.5 — Consumers read from local queue on own QM
# ===================================================================

class TestConsumersReadFromLocalQueueOnOwnQM:
    """Req 3.5: Consumers must read from local queues on their own QM."""

    def test_valid_consumer(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.validate_mq_constraints(topo)
        cons_violations = [
            v for v in violations
            if v.policy_id == "consumers_read_from_local_queue_on_own_qm"
        ]
        assert cons_violations == []

    def test_consumer_reads_from_remote_queue(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        rq = _queue("rq1", "RQ.A", QueueType.REMOTE, "QM_A")
        app = _app("app1", "BadConsumer", "QM_A", "consumer", consumes_from=["rq1"])
        topo = _make_topology(queue_managers=[qm], queues=[rq], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        cons_violations = [
            v for v in violations
            if v.policy_id == "consumers_read_from_local_queue_on_own_qm"
        ]
        assert len(cons_violations) >= 1
        assert any("remote" in v.description for v in cons_violations)

    def test_consumer_reads_from_queue_on_different_qm(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        lq = _queue("lq1", "LQ.B", QueueType.LOCAL, "QM_B")
        app = _app("app1", "CrossQMConsumer", "QM_A", "consumer", consumes_from=["lq1"])
        topo = _make_topology(queue_managers=[qm_a, qm_b], queues=[lq], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        cons_violations = [
            v for v in violations
            if v.policy_id == "consumers_read_from_local_queue_on_own_qm"
        ]
        assert len(cons_violations) >= 1

    def test_consumer_references_nonexistent_queue(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        app = _app("app1", "GhostConsumer", "QM_A", "consumer", consumes_from=["missing_q"])
        topo = _make_topology(queue_managers=[qm], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        cons_violations = [
            v for v in violations
            if v.policy_id == "consumers_read_from_local_queue_on_own_qm"
        ]
        assert len(cons_violations) >= 1
        assert "missing_q" in cons_violations[0].offending_objects


# ===================================================================
# Req 3.6 — Structured error on violation
# ===================================================================

class TestStructuredViolationErrors:
    """Req 3.6: Violations return structured errors with offending objects and remediation."""

    def test_violation_has_required_fields(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        app = _app("app1", "NoQMApp", "QM_MISSING", "producer")
        topo = _make_topology(queue_managers=[qm], applications=[app])
        violations = engine.validate_mq_constraints(topo)
        assert len(violations) >= 1
        v = violations[0]
        assert v.policy_id
        assert v.policy_name
        assert v.description
        assert isinstance(v.offending_objects, list)
        assert v.remediation_suggestion


# ===================================================================
# Req 9.1 — Sentinel policy evaluation
# ===================================================================

class TestSentinelPolicies:
    """Req 9.1: Sentinel policies including secure_by_default and neighbourhood_aligned."""

    def test_sentinel_passes_on_valid_topology(self, engine: PolicyEngine):
        topo = _valid_topology()
        violations = engine.evaluate_sentinel({"topology": topo})
        assert violations == []

    def test_secure_by_default_tls_disabled(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B",
                       metadata={"tls_enabled": False})
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        violations = engine.evaluate_sentinel({"topology": topo})
        sec_violations = [v for v in violations if v.policy_id == "secure_by_default"]
        assert len(sec_violations) >= 1
        assert "TLS" in sec_violations[0].description

    def test_secure_by_default_both_inhibited(self, engine: PolicyEngine):
        qm = _qm("QM_A")
        q = _queue("q1", "Q.DEAD", QueueType.LOCAL, "QM_A",
                    metadata={"inhibit_put": True, "inhibit_get": True})
        topo = _make_topology(queue_managers=[qm], queues=[q])
        violations = engine.evaluate_sentinel({"topology": topo})
        sec_violations = [v for v in violations if v.policy_id == "secure_by_default"]
        assert len(sec_violations) >= 1

    def test_neighbourhood_aligned_violation(self, engine: PolicyEngine):
        qm_a = _qm("QM_A", neighborhood="NORTH")
        qm_b = _qm("QM_B", neighborhood="SOUTH")
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B")
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        violations = engine.evaluate_sentinel({"topology": topo})
        nb_violations = [v for v in violations if v.policy_id == "neighbourhood_aligned"]
        assert len(nb_violations) == 1
        assert "NORTH" in nb_violations[0].description
        assert "SOUTH" in nb_violations[0].description

    def test_neighbourhood_aligned_same_neighbourhood(self, engine: PolicyEngine):
        qm_a = _qm("QM_A", neighborhood="NORTH")
        qm_b = _qm("QM_B", neighborhood="NORTH")
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B")
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        violations = engine.evaluate_sentinel({"topology": topo})
        nb_violations = [v for v in violations if v.policy_id == "neighbourhood_aligned"]
        assert nb_violations == []

    def test_sentinel_no_topology_returns_empty(self, engine: PolicyEngine):
        violations = engine.evaluate_sentinel({})
        assert violations == []


# ===================================================================
# Req 9.2 — OPA policy evaluation
# ===================================================================

class TestOPAPolicies:
    """Req 9.2: OPA policies for access control, data validation, and scope."""

    def test_access_control_admin_always_passes(self, engine: PolicyEngine):
        proposal = {"change_type": "delete", "change_description": "Remove QM"}
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        ac_violations = [v for v in violations if v.policy_id == "access_control_role_based"]
        assert ac_violations == []

    def test_access_control_missing_write_permission(self, engine: PolicyEngine):
        proposal = {"change_type": "create", "change_description": "Add queue"}
        actor = {"role": "viewer", "permissions": ["read"]}
        violations = engine.evaluate_opa(proposal, actor)
        ac_violations = [v for v in violations if v.policy_id == "access_control_role_based"]
        assert len(ac_violations) == 1
        assert "write" in ac_violations[0].description

    def test_access_control_delete_requires_admin(self, engine: PolicyEngine):
        proposal = {"change_type": "delete", "change_description": "Delete QM"}
        actor = {"role": "operator", "permissions": ["write"]}
        violations = engine.evaluate_opa(proposal, actor)
        ac_violations = [v for v in violations if v.policy_id == "access_control_role_based"]
        assert len(ac_violations) == 1
        assert "admin" in ac_violations[0].description

    def test_access_control_with_correct_permission(self, engine: PolicyEngine):
        proposal = {"change_type": "update", "change_description": "Update queue"}
        actor = {"role": "operator", "permissions": ["write"]}
        violations = engine.evaluate_opa(proposal, actor)
        ac_violations = [v for v in violations if v.policy_id == "access_control_role_based"]
        assert ac_violations == []

    def test_data_validation_missing_change_type(self, engine: PolicyEngine):
        proposal = {"change_description": "Something"}
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        dv_violations = [
            v for v in violations if v.policy_id == "data_validation_required_fields"
        ]
        assert len(dv_violations) == 1
        assert "change_type" in dv_violations[0].description

    def test_data_validation_missing_description(self, engine: PolicyEngine):
        proposal = {"change_type": "create"}
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        dv_violations = [
            v for v in violations if v.policy_id == "data_validation_required_fields"
        ]
        assert len(dv_violations) == 1
        assert "change_description" in dv_violations[0].description

    def test_data_validation_all_fields_present(self, engine: PolicyEngine):
        proposal = {"change_type": "create", "change_description": "Add queue"}
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        dv_violations = [
            v for v in violations if v.policy_id == "data_validation_required_fields"
        ]
        assert dv_violations == []

    def test_change_scope_within_limit(self, engine: PolicyEngine):
        proposal = {
            "change_type": "update",
            "change_description": "Small change",
            "affected_objects": ["obj1", "obj2"],
        }
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        scope_violations = [v for v in violations if v.policy_id == "change_scope_limit"]
        assert scope_violations == []

    def test_change_scope_exceeds_limit(self, engine: PolicyEngine):
        proposal = {
            "change_type": "update",
            "change_description": "Huge change",
            "affected_objects": [f"obj{i}" for i in range(60)],
            "max_scope": 50,
        }
        actor = {"role": "admin", "permissions": []}
        violations = engine.evaluate_opa(proposal, actor)
        scope_violations = [v for v in violations if v.policy_id == "change_scope_limit"]
        assert len(scope_violations) == 1
        assert "60" in scope_violations[0].description


# ===================================================================
# Req 9.3 — Combined evaluation (evaluate_all)
# ===================================================================

class TestCombinedEvaluation:
    """Req 9.3: evaluate_all runs both Sentinel and OPA policies."""

    def test_all_pass_on_valid_input(self, engine: PolicyEngine):
        topo = _valid_topology()
        proposal = {
            "topology": topo,
            "change_type": "create",
            "change_description": "Valid change",
        }
        actor = {"role": "admin", "permissions": []}
        passed, violations = engine.evaluate_all(proposal, actor)
        assert passed is True
        assert violations == []

    def test_all_fail_combines_sentinel_and_opa(self, engine: PolicyEngine):
        # Sentinel violation: TLS disabled
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B",
                       metadata={"tls_enabled": False})
        topo = _make_topology(queue_managers=[qm_a, qm_b], channels=[ch])
        # OPA violation: missing change_description
        proposal = {"topology": topo, "change_type": "create"}
        actor = {"role": "viewer", "permissions": ["read"]}
        passed, violations = engine.evaluate_all(proposal, actor)
        assert passed is False
        policy_ids = {v.policy_id for v in violations}
        assert "secure_by_default" in policy_ids
        assert "data_validation_required_fields" in policy_ids


# ===================================================================
# Compliance attestation generation
# ===================================================================

class TestComplianceAttestation:
    """Test compliance attestation generation (Req 9.4 context)."""

    def test_attestation_passes_on_valid_topology(self, engine: PolicyEngine):
        topo = _valid_topology()
        proposal = {
            "topology": topo,
            "change_type": "create",
            "change_description": "Valid change",
            "actor": {"role": "admin", "permissions": [], "name": "operator1"},
        }
        attestation = engine.generate_compliance_attestation(proposal)
        assert attestation["attested"] is True
        assert "attestation_id" in attestation
        assert attestation["result"] == "PASS"
        assert attestation["policy_count"] == len(engine.MQ_POLICIES) + len(engine.OPA_POLICIES)
        assert attestation["actor"] == "operator1"

    def test_attestation_fails_on_violations(self, engine: PolicyEngine):
        # Missing change_description → data validation violation
        proposal = {
            "change_type": "create",
            "actor": {"role": "viewer", "permissions": ["read"]},
        }
        attestation = engine.generate_compliance_attestation(proposal)
        assert attestation["attested"] is False
        assert attestation["violation_count"] > 0
        assert len(attestation["violations"]) > 0

    def test_attestation_includes_timestamp(self, engine: PolicyEngine):
        topo = _valid_topology()
        proposal = {
            "topology": topo,
            "change_type": "create",
            "change_description": "Valid change",
            "actor": {"role": "admin", "permissions": []},
        }
        attestation = engine.generate_compliance_attestation(proposal)
        assert "timestamp" in attestation

    def test_attestation_lists_evaluated_policies(self, engine: PolicyEngine):
        topo = _valid_topology()
        proposal = {
            "topology": topo,
            "change_type": "create",
            "change_description": "Valid change",
            "actor": {"role": "admin", "permissions": []},
        }
        attestation = engine.generate_compliance_attestation(proposal)
        assert "policies_evaluated" in attestation
        assert "exactly_one_qm_per_app" in attestation["policies_evaluated"]


# ===================================================================
# Edge cases and "both" role
# ===================================================================

class TestBothRoleApp:
    """Apps with role='both' must satisfy both producer and consumer constraints."""

    def test_both_role_valid(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        rq = _queue("rq1", "RQ.A.TO.B", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B", xmitq_id="xq1")
        xq = _queue("xq1", "XMITQ.A", QueueType.TRANSMISSION, "QM_A")
        lq = _queue("lq1", "LQ.A", QueueType.LOCAL, "QM_A")
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B")
        app = _app("app1", "BothApp", "QM_A", "both",
                    produces_to=["rq1"], consumes_from=["lq1"])
        topo = _make_topology(
            queue_managers=[qm_a, qm_b],
            queues=[rq, xq, lq],
            channels=[ch],
            applications=[app],
        )
        violations = engine.validate_mq_constraints(topo)
        assert violations == []

    def test_both_role_consumer_violation(self, engine: PolicyEngine):
        qm_a = _qm("QM_A")
        qm_b = _qm("QM_B")
        rq = _queue("rq1", "RQ.A.TO.B", QueueType.REMOTE, "QM_A",
                     target_qm_id="QM_B", xmitq_id="xq1")
        xq = _queue("xq1", "XMITQ.A", QueueType.TRANSMISSION, "QM_A")
        # Consumer reads from remote queue — violation
        ch = _channel("ch1", "QM_A.QM_B", ChannelDirection.SENDER, "QM_A", "QM_B")
        app = _app("app1", "BothBadConsumer", "QM_A", "both",
                    produces_to=["rq1"], consumes_from=["rq1"])
        topo = _make_topology(
            queue_managers=[qm_a, qm_b],
            queues=[rq, xq],
            channels=[ch],
            applications=[app],
        )
        violations = engine.validate_mq_constraints(topo)
        cons_violations = [
            v for v in violations
            if v.policy_id == "consumers_read_from_local_queue_on_own_qm"
        ]
        assert len(cons_violations) >= 1
