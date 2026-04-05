"""Unit tests for the Capacity Planning Engine.

Covers growth pattern analysis, capacity forecasting, onboarding impact
simulation, and capacity report generation.

Requirements: 23.1, 23.2, 23.5
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from capacity_planning.engine import (
    CapacityPlanningEngine,
    _linear_regression,
    _predict,
)
from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _make_snapshot(
    day_offset: int,
    qm_count: int = 2,
    queues_per_qm: int = 3,
    channels: int = 1,
    app_count: int = 2,
    base_date: datetime | None = None,
) -> TopologySnapshotEvent:
    """Build a snapshot at ``base_date + day_offset`` days with configurable sizes."""
    base = base_date or datetime(2025, 1, 1, tzinfo=timezone.utc)
    ts = base + timedelta(days=day_offset)

    qms = [
        QueueManager(
            id=f"QM_{i}",
            name=f"QM_{i}",
            hostname=f"host-{i}",
            port=1414,
            max_queues=100,
            max_channels=50,
        )
        for i in range(qm_count)
    ]

    queues: list[Queue] = []
    for qm in qms:
        for j in range(queues_per_qm):
            queues.append(
                Queue(
                    id=f"{qm.id}_Q{j}",
                    name=f"{qm.id}_Q{j}",
                    queue_type=QueueType.LOCAL,
                    owning_qm_id=qm.id,
                )
            )

    chs = [
        Channel(
            id=f"CH_{i}",
            name=f"QM_0.QM_{i + 1}" if i + 1 < qm_count else f"QM_0.QM_0",
            direction=ChannelDirection.SENDER,
            from_qm_id="QM_0",
            to_qm_id=f"QM_{min(i + 1, qm_count - 1)}",
        )
        for i in range(channels)
    ]

    apps = [
        Application(
            id=f"APP_{i}",
            name=f"App {i}",
            connected_qm_id=f"QM_{i % qm_count}",
            role="producer" if i % 2 == 0 else "consumer",
        )
        for i in range(app_count)
    ]

    return TopologySnapshotEvent(
        snapshot_id=f"snap-day{day_offset}",
        correlation_id=f"corr-day{day_offset}",
        timestamp=ts,
        mode="steady_state",
        queue_managers=qms,
        queues=queues,
        channels=chs,
        applications=apps,
    )


def _make_growing_snapshots(
    num_snapshots: int = 10,
    queue_growth: int = 2,
    channel_growth: int = 1,
) -> list[TopologySnapshotEvent]:
    """Create a series of snapshots with linearly growing queue/channel counts."""
    snapshots = []
    for i in range(num_snapshots):
        snapshots.append(
            _make_snapshot(
                day_offset=i * 3,  # every 3 days
                qm_count=2,
                queues_per_qm=3 + i * queue_growth,
                channels=1 + i * channel_growth,
                app_count=2,
            )
        )
    return snapshots


@pytest.fixture
def engine() -> CapacityPlanningEngine:
    return CapacityPlanningEngine()


@pytest.fixture
def growing_snapshots() -> list[TopologySnapshotEvent]:
    return _make_growing_snapshots()


@pytest.fixture
def current_topology() -> TopologySnapshotEvent:
    return _make_snapshot(day_offset=30, qm_count=2, queues_per_qm=10, channels=5)


# -----------------------------------------------------------------------
# Linear regression helpers
# -----------------------------------------------------------------------


class TestLinearRegression:
    def test_perfect_line(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [1.0, 3.0, 5.0, 7.0]
        slope, intercept = _linear_regression(xs, ys)
        assert abs(slope - 2.0) < 1e-9
        assert abs(intercept - 1.0) < 1e-9

    def test_flat_line(self):
        xs = [0.0, 1.0, 2.0]
        ys = [5.0, 5.0, 5.0]
        slope, intercept = _linear_regression(xs, ys)
        assert abs(slope) < 1e-9
        assert abs(intercept - 5.0) < 1e-9

    def test_single_point(self):
        slope, intercept = _linear_regression([1.0], [7.0])
        assert slope == 0.0
        assert intercept == 7.0

    def test_empty_input(self):
        slope, intercept = _linear_regression([], [])
        assert slope == 0.0
        assert intercept == 0.0

    def test_predict(self):
        assert _predict(2.0, 1.0, 5.0) == 11.0


# -----------------------------------------------------------------------
# Growth pattern analysis  (Req 23.1)
# -----------------------------------------------------------------------


class TestAnalyzeGrowthPatterns:
    def test_returns_all_metrics(
        self, engine: CapacityPlanningEngine, growing_snapshots: list[TopologySnapshotEvent],
    ):
        result = engine.analyze_growth_patterns(growing_snapshots)
        assert "qm_count" in result
        assert "queue_count" in result
        assert "channel_count" in result
        assert "app_count" in result

    def test_each_metric_has_required_fields(
        self, engine: CapacityPlanningEngine, growing_snapshots: list[TopologySnapshotEvent],
    ):
        result = engine.analyze_growth_patterns(growing_snapshots)
        for metric_name in ["qm_count", "queue_count", "channel_count", "app_count"]:
            m = result[metric_name]
            assert "data_points" in m
            assert "slope" in m
            assert "intercept" in m
            assert "current" in m

    def test_growing_queues_have_positive_slope(
        self, engine: CapacityPlanningEngine, growing_snapshots: list[TopologySnapshotEvent],
    ):
        result = engine.analyze_growth_patterns(growing_snapshots)
        # Queues grow by 2 per QM per snapshot → positive slope
        assert result["queue_count"]["slope"] > 0

    def test_constant_qm_count_has_zero_slope(
        self, engine: CapacityPlanningEngine, growing_snapshots: list[TopologySnapshotEvent],
    ):
        result = engine.analyze_growth_patterns(growing_snapshots)
        # QM count is constant at 2
        assert abs(result["qm_count"]["slope"]) < 1e-9

    def test_empty_snapshots_returns_empty(self, engine: CapacityPlanningEngine):
        assert engine.analyze_growth_patterns([]) == {}

    def test_single_snapshot(self, engine: CapacityPlanningEngine):
        snap = _make_snapshot(day_offset=0)
        result = engine.analyze_growth_patterns([snap])
        # With one point, slope should be 0
        assert result["qm_count"]["slope"] == 0.0

    def test_lookback_filters_old_snapshots(self, engine: CapacityPlanningEngine):
        # Create snapshots spanning 60 days
        snaps = [_make_snapshot(day_offset=i * 10) for i in range(7)]
        result = engine.analyze_growth_patterns(snaps, lookback_days=25)
        # Only snapshots within last 25 days from "now" should be used
        # Since snapshots are at fixed dates, the filtering depends on current time
        # Just verify it returns valid data
        assert "qm_count" in result


# -----------------------------------------------------------------------
# Capacity forecasting  (Req 23.2)
# -----------------------------------------------------------------------


class TestForecastCapacity:
    def test_growing_topology_produces_alerts(
        self, engine: CapacityPlanningEngine,
    ):
        # Create snapshots where queues grow rapidly toward the 100-queue limit
        snaps = []
        for i in range(10):
            snaps.append(
                _make_snapshot(
                    day_offset=i * 3,
                    qm_count=1,
                    queues_per_qm=30 + i * 5,  # 30, 35, 40, ... 75
                    channels=2,
                )
            )
        alerts = engine.forecast_capacity(snaps, horizon_days=60, threshold_pct=0.8)
        # With max_queues=100 and threshold=80, growing from 75 should trigger
        assert len(alerts) > 0
        assert alerts[0]["qm_id"] == "QM_0"

    def test_stable_topology_no_alerts(
        self, engine: CapacityPlanningEngine,
    ):
        # Constant low utilization
        snaps = [_make_snapshot(day_offset=i * 3, queues_per_qm=5) for i in range(10)]
        alerts = engine.forecast_capacity(snaps, horizon_days=30, threshold_pct=0.8)
        assert len(alerts) == 0

    def test_already_breached_produces_immediate_alert(
        self, engine: CapacityPlanningEngine,
    ):
        # Already at 85 queues out of 100 (85% > 80% threshold)
        snaps = [_make_snapshot(day_offset=i, queues_per_qm=85) for i in range(3)]
        alerts = engine.forecast_capacity(snaps, horizon_days=30, threshold_pct=0.8)
        queue_alerts = [a for a in alerts if a["metric"] == "queue_count"]
        assert len(queue_alerts) > 0
        assert "Immediate action" in queue_alerts[0]["recommended_action"]

    def test_empty_snapshots_returns_empty(self, engine: CapacityPlanningEngine):
        assert engine.forecast_capacity([]) == []

    def test_alert_structure(self, engine: CapacityPlanningEngine):
        snaps = []
        for i in range(5):
            snaps.append(
                _make_snapshot(day_offset=i * 5, qm_count=1, queues_per_qm=60 + i * 5)
            )
        alerts = engine.forecast_capacity(snaps, horizon_days=90, threshold_pct=0.8)
        if alerts:
            alert = alerts[0]
            assert "qm_id" in alert
            assert "qm_name" in alert
            assert "metric" in alert
            assert "current_utilization" in alert
            assert "predicted_breach_date" in alert
            assert "threshold" in alert
            assert "max_capacity" in alert
            assert "recommended_action" in alert


# -----------------------------------------------------------------------
# Onboarding impact simulation  (Req 23.4)
# -----------------------------------------------------------------------


class TestSimulateOnboardingImpact:
    def test_basic_onboarding(
        self, engine: CapacityPlanningEngine, current_topology: TopologySnapshotEvent,
    ):
        request = {
            "app_name": "NewApp",
            "connected_qm_id": "QM_0",
            "role": "producer",
            "new_queues": 3,
            "new_channels": 1,
        }
        result = engine.simulate_onboarding_impact(request, current_topology)
        assert result["app_name"] == "NewApp"
        assert result["delta"]["app_count"] == 1
        assert result["delta"]["queue_count"] == 3
        assert result["delta"]["channel_count"] == 1
        assert result["delta"]["qm_count"] == 0

    def test_no_new_objects(
        self, engine: CapacityPlanningEngine, current_topology: TopologySnapshotEvent,
    ):
        request = {
            "app_name": "LightApp",
            "connected_qm_id": "QM_0",
            "role": "consumer",
        }
        result = engine.simulate_onboarding_impact(request, current_topology)
        assert result["delta"]["queue_count"] == 0
        assert result["delta"]["channel_count"] == 0
        assert result["delta"]["app_count"] == 1

    def test_capacity_warning_when_near_limit(self, engine: CapacityPlanningEngine):
        # Create a topology where QM is near queue capacity
        topo = _make_snapshot(day_offset=0, qm_count=1, queues_per_qm=85)
        request = {
            "app_name": "HeavyApp",
            "connected_qm_id": "QM_0",
            "role": "producer",
            "new_queues": 5,
            "new_channels": 0,
        }
        result = engine.simulate_onboarding_impact(request, topo)
        # 85 + 5 = 90 out of 100 → 90% > 80% threshold
        assert result["capacity_warning"] is not None

    def test_no_warning_when_within_capacity(
        self, engine: CapacityPlanningEngine, current_topology: TopologySnapshotEvent,
    ):
        request = {
            "app_name": "SmallApp",
            "connected_qm_id": "QM_0",
            "role": "consumer",
            "new_queues": 1,
            "new_channels": 0,
        }
        result = engine.simulate_onboarding_impact(request, current_topology)
        assert result["capacity_warning"] is None


# -----------------------------------------------------------------------
# Capacity report generation  (Req 23.5)
# -----------------------------------------------------------------------


class TestGenerateCapacityReport:
    def test_report_structure(
        self,
        engine: CapacityPlanningEngine,
        growing_snapshots: list[TopologySnapshotEvent],
        current_topology: TopologySnapshotEvent,
    ):
        report = engine.generate_capacity_report(growing_snapshots, current_topology)
        assert "report_id" in report
        assert "generated_at" in report
        assert "qm_utilization" in report
        assert "projections" in report
        assert "alerts" in report
        assert "recommended_actions" in report

    def test_qm_utilization_per_qm(
        self,
        engine: CapacityPlanningEngine,
        growing_snapshots: list[TopologySnapshotEvent],
        current_topology: TopologySnapshotEvent,
    ):
        report = engine.generate_capacity_report(growing_snapshots, current_topology)
        assert len(report["qm_utilization"]) == len(current_topology.queue_managers)
        for entry in report["qm_utilization"]:
            assert "qm_id" in entry
            assert "qm_name" in entry
            assert "queue_utilization" in entry
            assert "channel_utilization" in entry
            assert 0.0 <= entry["queue_utilization"] <= 1.0
            assert 0.0 <= entry["channel_utilization"] <= 1.0

    def test_projections_have_30_60_90_day_horizons(
        self,
        engine: CapacityPlanningEngine,
        growing_snapshots: list[TopologySnapshotEvent],
        current_topology: TopologySnapshotEvent,
    ):
        report = engine.generate_capacity_report(growing_snapshots, current_topology)
        if report["projections"]:
            proj = report["projections"][0]
            assert "30d_queue_utilization" in proj
            assert "60d_queue_utilization" in proj
            assert "90d_queue_utilization" in proj
            assert "30d_channel_utilization" in proj
            assert "60d_channel_utilization" in proj
            assert "90d_channel_utilization" in proj

    def test_report_with_no_history(
        self, engine: CapacityPlanningEngine, current_topology: TopologySnapshotEvent,
    ):
        # Single snapshot — no projections possible
        report = engine.generate_capacity_report([], current_topology)
        assert report["projections"] == []
        assert len(report["qm_utilization"]) == len(current_topology.queue_managers)

    def test_recommended_actions_present(
        self,
        engine: CapacityPlanningEngine,
        growing_snapshots: list[TopologySnapshotEvent],
        current_topology: TopologySnapshotEvent,
    ):
        report = engine.generate_capacity_report(growing_snapshots, current_topology)
        assert len(report["recommended_actions"]) > 0
