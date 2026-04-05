"""Unit tests for the Explainable Decision Engine.

Covers report generation structure (Req 21.1, 21.7), reasoning chain
(Req 21.2), policy references (Req 21.3), alternatives considered
(Req 21.4), what-if evaluation (Req 21.6), and confidence scoring
(Req 7.11).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    DecisionReport,
    OperationalMode,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)
from decision_engine.engine import ExplainableDecisionEngine
from graph_store.neo4j_client import Neo4jGraphStore
from policy_engine.engine import PolicyEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topology(
    queue_managers: list[QueueManager] | None = None,
    queues: list[Queue] | None = None,
    channels: list[Channel] | None = None,
    applications: list[Application] | None = None,
) -> TopologySnapshotEvent:
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


def _qm(id: str, name: str | None = None, **kw) -> QueueManager:
    return QueueManager(id=id, name=name or id, hostname="localhost", port=1414, **kw)


def _queue(id: str, name: str, qt: QueueType, owning: str, **kw) -> Queue:
    return Queue(id=id, name=name, queue_type=qt, owning_qm_id=owning, **kw)


def _channel(id: str, name: str, from_qm: str, to_qm: str) -> Channel:
    return Channel(
        id=id, name=name, direction=ChannelDirection.SENDER,
        from_qm_id=from_qm, to_qm_id=to_qm,
    )


def _app(id: str, name: str, qm: str, role: str = "producer", **kw) -> Application:
    return Application(id=id, name=name, connected_qm_id=qm, role=role, **kw)


def _build_compliant_topology() -> TopologySnapshotEvent:
    """A small compliant topology for testing.

    Channel names use deterministic naming based on QM *ids*:
    sender on qm-a → ``qm-a.qm-b``, receiver on qm-b → ``qm-b.qm-a``.
    """
    qm_a = _qm("qm-a", "QM_A", neighborhood="north")
    qm_b = _qm("qm-b", "QM_B", neighborhood="north")

    local_q = _queue("lq1", "LQ1", QueueType.LOCAL, "qm-b")
    remote_q = _queue("rq1", "RQ1", QueueType.REMOTE, "qm-a",
                       target_qm_id="qm-b", xmitq_id="xq1")
    xmit_q = _queue("xq1", "XQ1", QueueType.TRANSMISSION, "qm-a")

    # Sender: from_qm_id=qm-a, to_qm_id=qm-b → name "qm-a.qm-b"
    ch_s = _channel("ch-s", "qm-a.qm-b", "qm-a", "qm-b")
    # Receiver: from_qm_id=qm-b, to_qm_id=qm-a → name "qm-b.qm-a"
    ch_r = Channel(id="ch-r", name="qm-b.qm-a", direction=ChannelDirection.RECEIVER,
                   from_qm_id="qm-b", to_qm_id="qm-a")

    app_prod = _app("app-p", "Producer", "qm-a", "producer", produces_to=["rq1"])
    app_cons = _app("app-c", "Consumer", "qm-b", "consumer", consumes_from=["lq1"])

    return _make_topology(
        queue_managers=[qm_a, qm_b],
        queues=[local_q, remote_q, xmit_q],
        channels=[ch_s, ch_r],
        applications=[app_prod, app_cons],
    )


def _compliant_proposal(**overrides) -> dict:
    """Build a change proposal dict that passes OPA data validation."""
    base = {
        "description": "Test change",
        "change_description": "Test change for unit testing",
        "change_type": "modify",
        "object_ids": [],
        "topology": _build_compliant_topology(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def graph_store() -> Neo4jGraphStore:
    """An in-memory graph store loaded with a compliant topology."""
    gs = Neo4jGraphStore()
    topo = _build_compliant_topology()
    gs.load_snapshot(topo, "v1")
    return gs


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def engine() -> ExplainableDecisionEngine:
    return ExplainableDecisionEngine()


# ---------------------------------------------------------------------------
# generate_report tests
# ---------------------------------------------------------------------------

class TestGenerateReport:
    """Tests for ExplainableDecisionEngine.generate_report (Req 21.1, 21.7)."""

    def test_report_returns_decision_report_model(
        self, engine, graph_store, policy_engine,
    ):
        proposal = _compliant_proposal(
            description="Add new queue",
            change_type="add_queue",
            object_ids=["qm-a"],
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)
        assert isinstance(report, DecisionReport)

    def test_report_has_all_required_fields(
        self, engine, graph_store, policy_engine,
    ):
        """Req 21.7 — structured JSON schema with required fields."""
        proposal = _compliant_proposal(
            description="Move app",
            change_type="move_app",
            object_ids=["qm-a"],
            correlation_id="corr-test-123",
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)

        assert report.report_id
        assert report.correlation_id == "corr-test-123"
        assert report.summary
        assert isinstance(report.reasoning_chain, list)
        assert len(report.reasoning_chain) > 0
        assert isinstance(report.policy_references, list)
        assert isinstance(report.alternatives_considered, list)
        assert report.risk_assessment
        assert report.recommendation

    def test_report_reasoning_chain_ordered(
        self, engine, graph_store, policy_engine,
    ):
        """Req 21.2 — reasoning chain shows sequential logic steps."""
        proposal = _compliant_proposal(
            description="Test change",
            change_type="test",
            object_ids=["qm-a"],
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)
        chain = report.reasoning_chain

        # First step should mention the proposal
        assert "proposal" in chain[0].lower() or "test" in chain[0].lower()
        # Should contain policy evaluation step
        assert any("polic" in step.lower() for step in chain)
        # Should contain blast radius step
        assert any("blast" in step.lower() for step in chain)
        # Should contain risk assessment step
        assert any("risk" in step.lower() for step in chain)

    def test_report_includes_policy_references(
        self, engine, graph_store, policy_engine,
    ):
        """Req 21.3 — references specific policies that influenced decision."""
        proposal = _compliant_proposal()
        report = engine.generate_report(proposal, graph_store, policy_engine)

        assert len(report.policy_references) > 0
        for ref in report.policy_references:
            assert "policy_id" in ref
            assert "description" in ref

    def test_report_includes_alternatives(
        self, engine, graph_store, policy_engine,
    ):
        """Req 21.4 — alternatives considered with rejection reasons."""
        proposal = _compliant_proposal(
            description="Multi-object change",
            object_ids=["qm-a", "qm-b"],
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)

        assert len(report.alternatives_considered) >= 1
        for alt in report.alternatives_considered:
            assert "action" in alt
            assert "rejection_reason" in alt

    def test_report_recommendation_approve_on_compliant(
        self, engine, graph_store, policy_engine,
    ):
        """Compliant proposal with low risk should recommend APPROVE."""
        proposal = _compliant_proposal(
            description="Small compliant change",
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)
        assert "APPROVE" in report.recommendation

    def test_report_recommendation_reject_on_violation(
        self, engine, graph_store, policy_engine,
    ):
        """Non-compliant proposal should recommend REJECT."""
        bad_topo = _make_topology(
            queue_managers=[_qm("qm-a", "QM_A"), _qm("qm-b", "QM_B")],
            applications=[
                _app("app-x", "BadApp", "qm-a", "both"),
                _app("app-x", "BadApp", "qm-b", "both"),  # same app on two QMs
            ],
        )
        proposal = _compliant_proposal(
            description="Bad change",
            topology=bad_topo,
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)
        assert "REJECT" in report.recommendation


# ---------------------------------------------------------------------------
# evaluate_what_if tests
# ---------------------------------------------------------------------------

class TestEvaluateWhatIf:
    """Tests for ExplainableDecisionEngine.evaluate_what_if (Req 21.6)."""

    def test_what_if_returns_required_keys(
        self, engine, graph_store, policy_engine,
    ):
        hypo = _compliant_proposal(
            description="What if we move App to QM_B?",
            change_type="move_app",
            object_ids=["qm-a"],
        )
        result = engine.evaluate_what_if(hypo, graph_store, policy_engine)

        assert "decision_report" in result
        assert "policy_passed" in result
        assert "violations" in result
        assert "blast_radius" in result
        assert "risk_assessment" in result
        assert "projected_outcome" in result
        assert "sandbox_session_id" in result

    def test_what_if_compliant_shows_compliant_outcome(
        self, engine, graph_store, policy_engine,
    ):
        hypo = _compliant_proposal(
            description="Hypothetical compliant change",
            change_type="test",
        )
        result = engine.evaluate_what_if(hypo, graph_store, policy_engine)
        assert result["policy_passed"] is True
        assert result["projected_outcome"] == "compliant"

    def test_what_if_non_compliant_shows_non_compliant(
        self, engine, graph_store, policy_engine,
    ):
        bad_topo = _make_topology(
            queue_managers=[_qm("qm-a", "QM_A"), _qm("qm-b", "QM_B")],
            applications=[
                _app("app-x", "App", "qm-a"),
                _app("app-x", "App", "qm-b"),  # same app on two QMs → violation
            ],
        )
        hypo = _compliant_proposal(topology=bad_topo, description="Bad hypothetical")
        result = engine.evaluate_what_if(hypo, graph_store, policy_engine)
        assert result["policy_passed"] is False
        assert result["projected_outcome"] == "non-compliant"
        assert len(result["violations"]) > 0

    def test_what_if_does_not_modify_production(
        self, engine, graph_store, policy_engine,
    ):
        """Production graph should be unchanged after what-if."""
        topo_before = graph_store.get_current_topology()
        qm_count_before = len(topo_before.queue_managers)

        hypo = _compliant_proposal(object_ids=["qm-a"])
        engine.evaluate_what_if(hypo, graph_store, policy_engine)

        topo_after = graph_store.get_current_topology()
        assert len(topo_after.queue_managers) == qm_count_before


# ---------------------------------------------------------------------------
# compute_confidence_score tests
# ---------------------------------------------------------------------------

class TestComputeConfidenceScore:
    """Tests for ExplainableDecisionEngine.compute_confidence_score (Req 7.11)."""

    def test_no_history_returns_neutral(self, engine):
        proposal = {"change_type": "add_queue", "object_ids": ["qm-a"]}
        score = engine.compute_confidence_score(proposal, [])
        assert score == 0.5

    def test_all_approved_returns_high(self, engine):
        proposal = {"change_type": "add_queue", "object_ids": ["qm-a"]}
        history = [
            {"change_type": "add_queue", "object_ids": ["qm-x"], "decision": "approve"},
            {"change_type": "add_queue", "object_ids": ["qm-y"], "decision": "approve"},
            {"change_type": "add_queue", "object_ids": ["qm-z"], "decision": "approve"},
        ]
        score = engine.compute_confidence_score(proposal, history)
        assert score == 1.0

    def test_all_rejected_returns_low(self, engine):
        proposal = {"change_type": "add_queue", "object_ids": ["qm-a"]}
        history = [
            {"change_type": "add_queue", "object_ids": ["qm-x"], "decision": "reject"},
            {"change_type": "add_queue", "object_ids": ["qm-y"], "decision": "reject"},
        ]
        score = engine.compute_confidence_score(proposal, history)
        assert score == 0.0

    def test_mixed_decisions_returns_ratio(self, engine):
        proposal = {"change_type": "modify", "object_ids": ["qm-a"]}
        history = [
            {"change_type": "modify", "object_ids": ["qm-x"], "decision": "approve"},
            {"change_type": "modify", "object_ids": ["qm-y"], "decision": "reject"},
            {"change_type": "modify", "object_ids": ["qm-z"], "decision": "approve"},
        ]
        score = engine.compute_confidence_score(proposal, history)
        # 2 approved out of 3 matching
        assert abs(score - 2 / 3) < 0.01

    def test_batch_approve_counts_as_approved(self, engine):
        proposal = {"change_type": "add_queue", "object_ids": ["qm-a"]}
        history = [
            {"change_type": "add_queue", "object_ids": ["qm-x"], "decision": "batch_approve"},
        ]
        score = engine.compute_confidence_score(proposal, history)
        assert score == 1.0

    def test_unrelated_history_ignored(self, engine):
        proposal = {"change_type": "add_queue", "object_ids": ["qm-a"]}
        history = [
            {"change_type": "delete_channel", "object_ids": ["ch-1"], "decision": "reject"},
        ]
        score = engine.compute_confidence_score(proposal, history)
        # No matching history → neutral
        assert score == 0.5

    def test_confidence_threshold_property(self):
        eng = ExplainableDecisionEngine(confidence_threshold=0.9)
        assert eng.confidence_threshold == 0.9

    def test_high_confidence_flagged_in_report(
        self, graph_store, policy_engine,
    ):
        """Req 7.11 — high-confidence proposals flagged in reasoning chain."""
        eng = ExplainableDecisionEngine(confidence_threshold=0.6)
        proposal = _compliant_proposal(
            description="Confident change",
            change_type="add_queue",
        )
        history = [
            {"change_type": "add_queue", "object_ids": [], "decision": "approve"},
            {"change_type": "add_queue", "object_ids": [], "decision": "approve"},
        ]
        report = eng.generate_report(proposal, graph_store, policy_engine, history)
        # Reasoning chain should mention "high" confidence
        assert any("high" in step.lower() for step in report.reasoning_chain)


# ---------------------------------------------------------------------------
# Risk assessment edge cases
# ---------------------------------------------------------------------------

class TestRiskAssessment:
    """Tests for risk assessment logic."""

    def test_explicit_risk_score_used(self, engine, graph_store, policy_engine):
        proposal = _compliant_proposal(
            description="Explicit risk",
            risk_score="critical",
        )
        report = engine.generate_report(proposal, graph_store, policy_engine)
        assert report.risk_assessment == "critical"

    def test_many_affected_objects_high_risk(self, engine, graph_store, policy_engine):
        """Many affected objects should yield high or critical risk."""
        # Build a connected topology so blast radius is large
        qm_a = _qm("qm-a", "QM_A")
        qm_b = _qm("qm-b", "QM_B")
        queues = [
            _queue(f"q-{i}", f"Q{i}", QueueType.LOCAL, "qm-a")
            for i in range(10)
        ]
        apps = [
            _app(f"app-{i}", f"App{i}", "qm-a", "consumer", consumes_from=[f"q-{i}"])
            for i in range(10)
        ]
        topo = _make_topology(
            queue_managers=[qm_a, qm_b],
            queues=queues,
            applications=apps,
        )
        gs = Neo4jGraphStore()
        gs.load_snapshot(topo, "v1")

        proposal = _compliant_proposal(
            description="Large change",
            object_ids=["qm-a"],
            topology=topo,
        )
        report = engine.generate_report(proposal, gs, policy_engine)
        assert report.risk_assessment in ("high", "critical")
