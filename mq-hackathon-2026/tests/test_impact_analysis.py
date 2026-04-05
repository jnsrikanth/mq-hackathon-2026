"""Unit tests for the Impact Analysis Engine.

Covers blast radius computation, risk scoring, downstream dependency
identification, rollback plan generation, and change simulation.

Requirements: 22.1, 22.2, 22.3, 22.4, 22.5
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graph_store.neo4j_client import Neo4jGraphStore
from impact_analysis.engine import ImpactAnalysisEngine
from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)
from policy_engine.engine import PolicyEngine


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

def _make_snapshot() -> TopologySnapshotEvent:
    """Build a small but realistic topology for testing.

    Topology:
      QM_A  ←  APP_PROD (producer)  → RQ1 (remote, on QM_A, target QM_B, xmitq=XQ1)
      QM_A hosts XQ1 (transmission)
      QM_B hosts LQ1 (local)  ←  APP_CONS (consumer)
      Channel CH1 sender from QM_A → QM_B
    """
    return TopologySnapshotEvent(
        snapshot_id="snap-1",
        correlation_id="corr-1",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        mode="bootstrap",
        queue_managers=[
            QueueManager(id="QM_A", name="QM_A", hostname="host-a", port=1414),
            QueueManager(id="QM_B", name="QM_B", hostname="host-b", port=1414),
        ],
        queues=[
            Queue(id="RQ1", name="RQ1", queue_type=QueueType.REMOTE,
                  owning_qm_id="QM_A", target_qm_id="QM_B", xmitq_id="XQ1"),
            Queue(id="XQ1", name="XQ1", queue_type=QueueType.TRANSMISSION,
                  owning_qm_id="QM_A"),
            Queue(id="LQ1", name="LQ1", queue_type=QueueType.LOCAL,
                  owning_qm_id="QM_B"),
        ],
        channels=[
            Channel(id="CH1", name="QM_A.QM_B", direction=ChannelDirection.SENDER,
                    from_qm_id="QM_A", to_qm_id="QM_B"),
        ],
        applications=[
            Application(id="APP_PROD", name="Producer App",
                        connected_qm_id="QM_A", role="producer",
                        produces_to=["RQ1"], criticality="high"),
            Application(id="APP_CONS", name="Consumer App",
                        connected_qm_id="QM_B", role="consumer",
                        consumes_from=["LQ1"], criticality="low"),
        ],
    )


@pytest.fixture
def graph_store() -> Neo4jGraphStore:
    gs = Neo4jGraphStore()
    gs.load_snapshot(_make_snapshot(), version_tag="v1")
    return gs


@pytest.fixture
def engine() -> ImpactAnalysisEngine:
    return ImpactAnalysisEngine()


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def topology() -> TopologySnapshotEvent:
    return _make_snapshot()


# -----------------------------------------------------------------------
# Blast radius  (Req 22.1)
# -----------------------------------------------------------------------

class TestComputeBlastRadius:
    def test_single_seed_returns_direct_and_transitive(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        proposal = {"object_ids": ["QM_A"]}
        result = engine.compute_blast_radius(proposal, graph_store)

        assert "direct" in result
        assert "transitive" in result
        assert "total_affected" in result
        assert result["total_affected"] == len(result["direct"]) + len(result["transitive"])
        # QM_A should have direct neighbours (queues, channels, apps)
        assert result["total_affected"] > 0

    def test_empty_seed_returns_empty(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        result = engine.compute_blast_radius({"object_ids": []}, graph_store)
        assert result["direct"] == []
        assert result["transitive"] == []
        assert result["total_affected"] == 0

    def test_nonexistent_seed_returns_empty(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        result = engine.compute_blast_radius({"object_ids": ["DOES_NOT_EXIST"]}, graph_store)
        assert result["total_affected"] == 0

    def test_multiple_seeds(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        result = engine.compute_blast_radius({"object_ids": ["QM_A", "QM_B"]}, graph_store)
        assert result["total_affected"] > 0
        # Both QMs touched — should cover most of the topology
        all_ids = {o["id"] for o in result["direct"] + result["transitive"]}
        assert "CH1" in all_ids or "APP_PROD" in all_ids


# -----------------------------------------------------------------------
# Risk scoring  (Req 22.2)
# -----------------------------------------------------------------------

class TestComputeRiskScore:
    def test_zero_affected_is_low(self, engine: ImpactAnalysisEngine):
        blast = {"direct": [], "transitive": [], "total_affected": 0}
        assert engine.compute_risk_score(blast) == "low"

    def test_small_blast_no_critical_apps_is_low(self, engine: ImpactAnalysisEngine):
        blast = {
            "direct": [{"id": "x", "label": "Queue", "name": "q"}],
            "transitive": [],
            "total_affected": 1,
        }
        assert engine.compute_risk_score(blast) == "low"

    def test_medium_blast_with_medium_criticality(self, engine: ImpactAnalysisEngine):
        objs = [{"id": f"o{i}", "label": "Queue", "name": f"q{i}"} for i in range(6)]
        blast = {"direct": objs, "transitive": [], "total_affected": 6}
        crit = {"o0": "medium"}
        score = engine.compute_risk_score(blast, app_criticality=crit)
        assert score in ("medium", "high")

    def test_large_blast_with_critical_app_is_critical(self, engine: ImpactAnalysisEngine):
        objs = [{"id": f"o{i}", "label": "App", "name": f"a{i}"} for i in range(20)]
        blast = {"direct": objs, "transitive": [], "total_affected": 20}
        crit = {"o0": "critical"}
        score = engine.compute_risk_score(blast, app_criticality=crit)
        assert score in ("high", "critical")

    def test_historical_failures_increase_risk(self, engine: ImpactAnalysisEngine):
        objs = [{"id": f"o{i}", "label": "Queue", "name": f"q{i}"} for i in range(6)]
        blast = {"direct": objs, "transitive": [], "total_affected": 6}
        history = [{"outcome": "failure"}] * 5 + [{"outcome": "success"}] * 5
        score = engine.compute_risk_score(blast, historical_outcomes=history)
        # 50% failure rate should push score up
        assert score in ("high", "critical")

    def test_no_history_no_criticality_moderate_count(self, engine: ImpactAnalysisEngine):
        # 16 objects → count_score 3, no crit/history → composite 3 → medium
        objs = [{"id": f"o{i}", "label": "Queue", "name": f"q{i}"} for i in range(16)]
        blast = {"direct": objs, "transitive": [], "total_affected": 16}
        score = engine.compute_risk_score(blast)
        assert score == "medium"


# -----------------------------------------------------------------------
# Downstream dependencies  (Req 22.3)
# -----------------------------------------------------------------------

class TestIdentifyDownstreamDependencies:
    def test_qm_returns_consumers_channels_xmitqs(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        proposal = {"object_ids": ["QM_B"]}
        deps = engine.identify_downstream_dependencies(proposal, graph_store)
        assert "consumers" in deps
        assert "channels" in deps
        assert "xmitqs" in deps
        # APP_CONS consumes from LQ1 on QM_B
        consumer_ids = {c["id"] for c in deps["consumers"]}
        assert "APP_CONS" in consumer_ids

    def test_empty_ids_returns_empty(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        deps = engine.identify_downstream_dependencies({"object_ids": []}, graph_store)
        assert deps["consumers"] == []
        assert deps["channels"] == []
        assert deps["xmitqs"] == []

    def test_qm_a_has_xmitq(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
    ):
        deps = engine.identify_downstream_dependencies({"object_ids": ["QM_A"]}, graph_store)
        xmitq_ids = {x["id"] for x in deps["xmitqs"]}
        assert "XQ1" in xmitq_ids


# -----------------------------------------------------------------------
# Rollback plan  (Req 22.4)
# -----------------------------------------------------------------------

class TestGenerateRollbackPlan:
    def test_delete_action_produces_create_rollback(
        self, engine: ImpactAnalysisEngine, topology: TopologySnapshotEvent,
    ):
        proposal = {
            "actions": [
                {"type": "delete", "object_type": "queue", "target_id": "LQ1"},
            ],
        }
        plan = engine.generate_rollback_plan(proposal, topology)
        assert "rollback_id" in plan
        assert len(plan["actions"]) == 1
        rb = plan["actions"][0]
        assert rb["type"] == "create"
        assert rb["target_id"] == "LQ1"
        assert rb["restore_data"]["id"] == "LQ1"

    def test_create_action_produces_delete_rollback(
        self, engine: ImpactAnalysisEngine, topology: TopologySnapshotEvent,
    ):
        proposal = {
            "actions": [
                {"type": "create", "object_type": "queue", "target_id": "NEW_Q"},
            ],
        }
        plan = engine.generate_rollback_plan(proposal, topology)
        assert plan["actions"][0]["type"] == "delete"
        assert plan["actions"][0]["target_id"] == "NEW_Q"

    def test_update_action_produces_restore_rollback(
        self, engine: ImpactAnalysisEngine, topology: TopologySnapshotEvent,
    ):
        proposal = {
            "actions": [
                {"type": "update", "object_type": "queue_manager", "target_id": "QM_A"},
            ],
        }
        plan = engine.generate_rollback_plan(proposal, topology)
        rb = plan["actions"][0]
        assert rb["type"] == "update"
        assert rb["restore_data"]["id"] == "QM_A"

    def test_unknown_action_produces_noop(
        self, engine: ImpactAnalysisEngine, topology: TopologySnapshotEvent,
    ):
        proposal = {
            "actions": [
                {"type": "migrate", "object_type": "queue", "target_id": "LQ1"},
            ],
        }
        plan = engine.generate_rollback_plan(proposal, topology)
        assert plan["actions"][0]["type"] == "noop"

    def test_original_state_summary(
        self, engine: ImpactAnalysisEngine, topology: TopologySnapshotEvent,
    ):
        plan = engine.generate_rollback_plan({"actions": []}, topology)
        summary = plan["original_state"]
        assert summary["snapshot_id"] == "snap-1"
        assert summary["qm_count"] == 2
        assert summary["queue_count"] == 3
        assert summary["channel_count"] == 1
        assert summary["app_count"] == 2


# -----------------------------------------------------------------------
# Change simulation  (Req 22.5)
# -----------------------------------------------------------------------

class TestSimulateChange:
    def test_valid_change_passes_constraints(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ):
        # Update a QM name — should not break any constraints
        proposal = {
            "actions": [
                {"type": "update", "object_type": "queue_manager",
                 "target_id": "QM_A", "data": {"name": "QM_A_RENAMED"}},
            ],
        }
        result = engine.simulate_change(proposal, graph_store, policy_engine)
        assert result["valid"] is True
        assert result["violations"] == []
        assert result["post_change_topology_summary"]["qm_count"] == 2

    def test_deleting_qm_causes_violations(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ):
        # Deleting QM_A should break constraints (apps reference it)
        proposal = {
            "actions": [
                {"type": "delete", "object_type": "queue_manager", "target_id": "QM_A"},
            ],
        }
        result = engine.simulate_change(proposal, graph_store, policy_engine)
        assert result["valid"] is False
        assert len(result["violations"]) > 0
        assert result["post_change_topology_summary"]["qm_count"] == 1

    def test_simulation_does_not_modify_production(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ):
        topo_before = graph_store.get_current_topology()
        proposal = {
            "actions": [
                {"type": "delete", "object_type": "queue_manager", "target_id": "QM_A"},
            ],
        }
        engine.simulate_change(proposal, graph_store, policy_engine)
        topo_after = graph_store.get_current_topology()
        assert len(topo_before.queue_managers) == len(topo_after.queue_managers)

    def test_create_action_in_simulation(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ):
        proposal = {
            "actions": [
                {"type": "create", "object_type": "queue_manager",
                 "target_id": "QM_C",
                 "data": {"name": "QM_C", "hostname": "host-c", "port": 1414}},
            ],
        }
        result = engine.simulate_change(proposal, graph_store, policy_engine)
        assert result["post_change_topology_summary"]["qm_count"] == 3

    def test_empty_actions_is_valid(
        self, engine: ImpactAnalysisEngine, graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ):
        result = engine.simulate_change({"actions": []}, graph_store, policy_engine)
        assert result["valid"] is True
