"""Unit tests for the Topology Optimizer.

Tests consolidation candidate identification, channel elimination,
clustering suggestions, placement recommendation, and combined
improvement calculation.

Requirements: 24.1, 24.2, 24.3, 24.4, 24.5
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graph_store.neo4j_client import Neo4jGraphStore
from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)
from optimizer.engine import TopologyOptimizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    qms: list[QueueManager],
    queues: list[Queue] | None = None,
    channels: list[Channel] | None = None,
    apps: list[Application] | None = None,
) -> TopologySnapshotEvent:
    return TopologySnapshotEvent(
        snapshot_id="snap-1",
        correlation_id="corr-1",
        timestamp=datetime.now(timezone.utc),
        mode="bootstrap",
        queue_managers=qms,
        queues=queues or [],
        channels=channels or [],
        applications=apps or [],
    )


def _load(graph_store: Neo4jGraphStore, snapshot: TopologySnapshotEvent) -> None:
    graph_store.load_snapshot(snapshot, "v1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def optimizer() -> TopologyOptimizer:
    return TopologyOptimizer()


@pytest.fixture
def graph_store() -> Neo4jGraphStore:
    return Neo4jGraphStore()


# ---------------------------------------------------------------------------
# Tests: identify_consolidation_candidates  (Req 24.1)
# ---------------------------------------------------------------------------


class TestConsolidationCandidates:
    """Validates: Requirement 24.1"""

    def test_same_neighborhood_qms_are_candidates(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """Two QMs in the same neighbourhood with spare capacity are candidates."""
        qm_a = QueueManager(id="qm1", name="QM_A", hostname="h1", port=1414, neighborhood="east")
        qm_b = QueueManager(id="qm2", name="QM_B", hostname="h2", port=1414, neighborhood="east")
        ch = Channel(id="ch1", name="QM_A.QM_B", direction=ChannelDirection.SENDER, from_qm_id="qm1", to_qm_id="qm2")

        _load(graph_store, _make_snapshot([qm_a, qm_b], channels=[ch]))

        candidates = optimizer.identify_consolidation_candidates(graph_store)
        assert len(candidates) >= 1
        c = candidates[0]
        assert {c["source_qm_id"], c["target_qm_id"]} == {"qm1", "qm2"}
        assert c["projected_channel_reduction"] >= 1

    def test_different_neighborhoods_not_candidates(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """QMs in different neighbourhoods should not be consolidation candidates."""
        qm_a = QueueManager(id="qm1", name="QM_A", hostname="h1", port=1414, neighborhood="east")
        qm_b = QueueManager(id="qm2", name="QM_B", hostname="h2", port=1414, neighborhood="west")
        ch = Channel(id="ch1", name="QM_A.QM_B", direction=ChannelDirection.SENDER, from_qm_id="qm1", to_qm_id="qm2")

        _load(graph_store, _make_snapshot([qm_a, qm_b], channels=[ch]))

        candidates = optimizer.identify_consolidation_candidates(graph_store)
        assert len(candidates) == 0

    def test_capacity_exceeded_not_candidate(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """QMs whose combined queues exceed capacity should not be candidates."""
        qm_a = QueueManager(id="qm1", name="QM_A", hostname="h1", port=1414, neighborhood="east", max_queues=3)
        qm_b = QueueManager(id="qm2", name="QM_B", hostname="h2", port=1414, neighborhood="east", max_queues=3)
        queues = [
            Queue(id=f"q{i}", name=f"Q{i}", queue_type=QueueType.LOCAL, owning_qm_id="qm1")
            for i in range(2)
        ] + [
            Queue(id=f"q{i+2}", name=f"Q{i+2}", queue_type=QueueType.LOCAL, owning_qm_id="qm2")
            for i in range(2)
        ]

        _load(graph_store, _make_snapshot([qm_a, qm_b], queues=queues))

        candidates = optimizer.identify_consolidation_candidates(graph_store)
        # 4 queues > max_queues of 3 → should not be a candidate
        assert len(candidates) == 0

    def test_empty_topology_returns_empty(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        _load(graph_store, _make_snapshot([]))
        assert optimizer.identify_consolidation_candidates(graph_store) == []


# ---------------------------------------------------------------------------
# Tests: identify_channel_elimination  (Req 24.2)
# ---------------------------------------------------------------------------


class TestChannelElimination:
    """Validates: Requirement 24.2"""

    def test_channel_with_common_intermediate(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """A channel between A-C is eliminable if A-B and B-C both exist."""
        qms = [
            QueueManager(id="qm_a", name="QM_A", hostname="h1", port=1414),
            QueueManager(id="qm_b", name="QM_B", hostname="h2", port=1414),
            QueueManager(id="qm_c", name="QM_C", hostname="h3", port=1414),
        ]
        channels = [
            Channel(id="ch_ab", name="A.B", direction=ChannelDirection.SENDER, from_qm_id="qm_a", to_qm_id="qm_b"),
            Channel(id="ch_bc", name="B.C", direction=ChannelDirection.SENDER, from_qm_id="qm_b", to_qm_id="qm_c"),
            Channel(id="ch_ac", name="A.C", direction=ChannelDirection.SENDER, from_qm_id="qm_a", to_qm_id="qm_c"),
        ]
        _load(graph_store, _make_snapshot(qms, channels=channels))

        candidates = optimizer.identify_channel_elimination(graph_store)
        # ch_ac should be eliminable via qm_b
        ac_candidates = [c for c in candidates if c["channel_id"] == "ch_ac"]
        assert len(ac_candidates) == 1
        assert ac_candidates[0]["reroute_via_qm_id"] == "qm_b"
        assert ac_candidates[0]["new_hops"] == 2

    def test_no_elimination_without_intermediate(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """No elimination candidates when there's no common intermediate."""
        qms = [
            QueueManager(id="qm_a", name="QM_A", hostname="h1", port=1414),
            QueueManager(id="qm_b", name="QM_B", hostname="h2", port=1414),
        ]
        channels = [
            Channel(id="ch_ab", name="A.B", direction=ChannelDirection.SENDER, from_qm_id="qm_a", to_qm_id="qm_b"),
        ]
        _load(graph_store, _make_snapshot(qms, channels=channels))

        candidates = optimizer.identify_channel_elimination(graph_store)
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Tests: suggest_clustering_optimizations  (Req 24.3)
# ---------------------------------------------------------------------------


class TestClusteringOptimizations:
    """Validates: Requirement 24.3"""

    def test_suggests_reassignment_when_majority_channels_elsewhere(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """QM with more channels to another neighbourhood should be suggested for reassignment."""
        qms = [
            QueueManager(id="qm1", name="QM1", hostname="h1", port=1414, neighborhood="east"),
            QueueManager(id="qm2", name="QM2", hostname="h2", port=1414, neighborhood="west"),
            QueueManager(id="qm3", name="QM3", hostname="h3", port=1414, neighborhood="west"),
        ]
        # qm1 (east) has 2 channels to west, 0 to east
        channels = [
            Channel(id="ch1", name="QM1.QM2", direction=ChannelDirection.SENDER, from_qm_id="qm1", to_qm_id="qm2"),
            Channel(id="ch2", name="QM1.QM3", direction=ChannelDirection.SENDER, from_qm_id="qm1", to_qm_id="qm3"),
        ]
        _load(graph_store, _make_snapshot(qms, channels=channels))

        suggestions = optimizer.suggest_clustering_optimizations(graph_store)
        qm1_suggestions = [s for s in suggestions if s["qm_id"] == "qm1"]
        assert len(qm1_suggestions) == 1
        assert qm1_suggestions[0]["suggested_neighborhood"] == "west"

    def test_no_suggestion_when_well_clustered(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """No suggestions when QMs are already well-clustered."""
        qms = [
            QueueManager(id="qm1", name="QM1", hostname="h1", port=1414, neighborhood="east"),
            QueueManager(id="qm2", name="QM2", hostname="h2", port=1414, neighborhood="east"),
        ]
        channels = [
            Channel(id="ch1", name="QM1.QM2", direction=ChannelDirection.SENDER, from_qm_id="qm1", to_qm_id="qm2"),
        ]
        _load(graph_store, _make_snapshot(qms, channels=channels))

        suggestions = optimizer.suggest_clustering_optimizations(graph_store)
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: recommend_placement  (Req 24.4)
# ---------------------------------------------------------------------------


class TestRecommendPlacement:
    """Validates: Requirement 24.4"""

    def test_prefers_same_neighborhood(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """Placement should prefer a QM in the requested neighbourhood."""
        qms = [
            QueueManager(id="qm1", name="QM_East", hostname="h1", port=1414, neighborhood="east"),
            QueueManager(id="qm2", name="QM_West", hostname="h2", port=1414, neighborhood="west"),
        ]
        _load(graph_store, _make_snapshot(qms))

        result = optimizer.recommend_placement(
            {"app_name": "NewApp", "neighborhood": "east", "role": "producer"},
            graph_store,
        )
        assert result["recommended_qm_id"] == "qm1"
        assert result["score_breakdown"]["neighborhood_affinity"] == 1.0

    def test_prefers_qm_with_partner_connectivity(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """Placement should prefer a QM already connected to communication partners.

        qm1 hosts both partners. qm2 and qm3 are isolated (no channels).
        qm1 should win on connectivity because it directly reaches both
        partner QMs (itself), while qm2/qm3 reach neither.
        """
        qms = [
            QueueManager(id="qm1", name="QM1", hostname="h1", port=1414),
            QueueManager(id="qm2", name="QM2", hostname="h2", port=1414),
            QueueManager(id="qm3", name="QM3", hostname="h3", port=1414),
        ]
        apps = [
            Application(id="app1", name="PartnerA", connected_qm_id="qm1", role="producer"),
            Application(id="app2", name="PartnerB", connected_qm_id="qm1", role="consumer"),
        ]
        _load(graph_store, _make_snapshot(qms, apps=apps))

        result = optimizer.recommend_placement(
            {"app_name": "NewApp", "communicates_with": ["app1", "app2"], "role": "consumer"},
            graph_store,
        )
        # qm1 hosts both partners → connectivity = 1.0; qm2/qm3 reach neither → 0.0
        assert result["recommended_qm_id"] == "qm1"
        assert result["score_breakdown"]["connectivity"] == 1.0

    def test_empty_topology_returns_none(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        _load(graph_store, _make_snapshot([]))
        result = optimizer.recommend_placement(
            {"app_name": "NewApp", "role": "producer"}, graph_store
        )
        assert result["recommended_qm_id"] is None

    def test_alternatives_are_returned(
        self, optimizer: TopologyOptimizer, graph_store: Neo4jGraphStore
    ) -> None:
        """Result should include alternative QMs."""
        qms = [
            QueueManager(id="qm1", name="QM1", hostname="h1", port=1414),
            QueueManager(id="qm2", name="QM2", hostname="h2", port=1414),
            QueueManager(id="qm3", name="QM3", hostname="h3", port=1414),
        ]
        _load(graph_store, _make_snapshot(qms))

        result = optimizer.recommend_placement(
            {"app_name": "NewApp", "role": "producer"}, graph_store
        )
        assert len(result["alternatives"]) == 2


# ---------------------------------------------------------------------------
# Tests: calculate_combined_improvement  (Req 24.5)
# ---------------------------------------------------------------------------


class TestCombinedImprovement:
    """Validates: Requirement 24.5"""

    def test_sums_channel_reductions(self, optimizer: TopologyOptimizer) -> None:
        recs = [
            {"type": "consolidation", "projected_channel_reduction": 3},
            {"type": "channel_elimination", "projected_channel_reduction": 2},
        ]
        result = optimizer.calculate_combined_improvement(recs)
        assert result["combined_channel_reduction"] == 5
        assert result["recommendation_count"] == 2
        assert len(result["individual"]) == 2

    def test_empty_recommendations(self, optimizer: TopologyOptimizer) -> None:
        result = optimizer.calculate_combined_improvement([])
        assert result["combined_channel_reduction"] == 0
        assert result["recommendation_count"] == 0

    def test_individual_entries_have_correct_types(
        self, optimizer: TopologyOptimizer
    ) -> None:
        recs = [
            {"type": "consolidation", "projected_channel_reduction": 4, "description": "Merge QM1 into QM2"},
        ]
        result = optimizer.calculate_combined_improvement(recs)
        entry = result["individual"][0]
        assert entry["type"] == "consolidation"
        assert entry["channel_reduction"] == 4
        assert entry["description"] == "Merge QM1 into QM2"
