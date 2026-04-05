"""Capacity Planning Engine for the MQ Guardian Platform.

Analyzes historical topology growth patterns, forecasts capacity threshold
breaches using simple linear regression, simulates onboarding impact, and
generates periodic capacity reports.

All forecasting uses pure-Python least-squares linear regression — no
external ML libraries required.

Requirements: 23.1, 23.2, 23.3, 23.4, 23.5, 23.6
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from models.domain import (
    CapacityAlert,
    QueueManager,
    TopologySnapshotEvent,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Pure-Python linear regression helpers
# =====================================================================


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least-squares fit: y = slope * x + intercept.

    Returns ``(slope, intercept)``.  If the input has fewer than 2 points
    or zero variance in *x*, returns ``(0.0, mean_y)``.
    """
    n = len(xs)
    if n < 2:
        mean_y = sum(ys) / n if n else 0.0
        return 0.0, mean_y

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))

    if ss_xx == 0:
        return 0.0, mean_y

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _predict(slope: float, intercept: float, x: float) -> float:
    """Predict y for a given x using a linear model."""
    return slope * x + intercept


# =====================================================================
# Capacity Planning Engine
# =====================================================================


class CapacityPlanningEngine:
    """Analyzes growth patterns and predicts future capacity needs.

    All methods are stateless — they accept snapshot history and topology
    data as arguments so the caller controls the lifecycle.
    """

    # -----------------------------------------------------------------
    # Growth pattern analysis  (Req 23.1)
    # -----------------------------------------------------------------

    def analyze_growth_patterns(
        self,
        snapshots: list[TopologySnapshotEvent],
        lookback_days: int = 30,
    ) -> dict:
        """Analyze QM count, queue count, channel count, and app count trends.

        ``snapshots`` should be a list of historical topology snapshots
        ordered by timestamp.  Only snapshots within the last
        ``lookback_days`` are considered.

        Returns a dict keyed by metric name, each containing:
        - ``data_points``: list of ``{"day_offset": float, "value": int}``
        - ``slope``: daily growth rate
        - ``intercept``: baseline value
        - ``current``: most recent value
        """
        if not snapshots:
            return {}

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        filtered = [s for s in snapshots if s.timestamp >= cutoff]
        if not filtered:
            filtered = snapshots  # fallback: use all available data

        # Sort by timestamp
        filtered.sort(key=lambda s: s.timestamp)
        t0 = filtered[0].timestamp

        metrics: dict[str, list[tuple[float, int]]] = {
            "qm_count": [],
            "queue_count": [],
            "channel_count": [],
            "app_count": [],
        }

        for snap in filtered:
            day_offset = (snap.timestamp - t0).total_seconds() / 86400.0
            metrics["qm_count"].append((day_offset, len(snap.queue_managers)))
            metrics["queue_count"].append((day_offset, len(snap.queues)))
            metrics["channel_count"].append((day_offset, len(snap.channels)))
            metrics["app_count"].append((day_offset, len(snap.applications)))

        result: dict[str, Any] = {}
        for metric_name, points in metrics.items():
            xs = [p[0] for p in points]
            ys = [float(p[1]) for p in points]
            slope, intercept = _linear_regression(xs, ys)
            result[metric_name] = {
                "data_points": [
                    {"day_offset": x, "value": int(y)} for x, y in points
                ],
                "slope": slope,
                "intercept": intercept,
                "current": int(ys[-1]) if ys else 0,
            }

        return result

    # -----------------------------------------------------------------
    # Capacity forecasting  (Req 23.2, 23.3)
    # -----------------------------------------------------------------

    def forecast_capacity(
        self,
        snapshots: list[TopologySnapshotEvent],
        horizon_days: int = 30,
        threshold_pct: float = 0.8,
    ) -> list[dict]:
        """Forecast when QMs will reach capacity thresholds.

        Uses linear trend extrapolation on per-QM queue and channel
        counts.  Returns a list of alert dicts for QMs predicted to
        breach within ``horizon_days``.

        Each alert contains:
        - ``qm_id``, ``qm_name``
        - ``metric`` (``"queue_count"`` or ``"channel_count"``)
        - ``current_utilization`` (0.0–1.0)
        - ``predicted_breach_date`` (ISO string)
        - ``threshold``
        - ``max_capacity``
        - ``recommended_action``
        """
        if not snapshots:
            return []

        snapshots_sorted = sorted(snapshots, key=lambda s: s.timestamp)
        t0 = snapshots_sorted[0].timestamp
        now = snapshots_sorted[-1].timestamp

        # Build per-QM time series for queue and channel counts
        qm_series: dict[str, dict] = {}  # qm_id -> {info, queue_ts, channel_ts}

        for snap in snapshots_sorted:
            day_offset = (snap.timestamp - t0).total_seconds() / 86400.0
            qm_map = {qm.id: qm for qm in snap.queue_managers}

            # Count queues per QM
            queues_per_qm: dict[str, int] = {}
            for q in snap.queues:
                queues_per_qm[q.owning_qm_id] = queues_per_qm.get(q.owning_qm_id, 0) + 1

            # Count channels per QM (from_qm side)
            channels_per_qm: dict[str, int] = {}
            for ch in snap.channels:
                channels_per_qm[ch.from_qm_id] = channels_per_qm.get(ch.from_qm_id, 0) + 1

            for qm in snap.queue_managers:
                if qm.id not in qm_series:
                    qm_series[qm.id] = {
                        "name": qm.name,
                        "max_queues": qm.max_queues,
                        "max_channels": qm.max_channels,
                        "queue_ts": [],
                        "channel_ts": [],
                    }
                entry = qm_series[qm.id]
                entry["queue_ts"].append(
                    (day_offset, queues_per_qm.get(qm.id, 0))
                )
                entry["channel_ts"].append(
                    (day_offset, channels_per_qm.get(qm.id, 0))
                )

        alerts: list[dict] = []
        now_offset = (now - t0).total_seconds() / 86400.0

        for qm_id, info in qm_series.items():
            for metric, ts_key, max_key in [
                ("queue_count", "queue_ts", "max_queues"),
                ("channel_count", "channel_ts", "max_channels"),
            ]:
                ts = info[ts_key]
                if not ts:
                    continue

                xs = [p[0] for p in ts]
                ys = [float(p[1]) for p in ts]
                slope, intercept = _linear_regression(xs, ys)

                max_cap = info[max_key]
                threshold_val = threshold_pct * max_cap
                current_val = ys[-1]
                current_util = current_val / max_cap if max_cap > 0 else 0.0

                # Already breached?
                if current_val >= threshold_val:
                    alerts.append({
                        "qm_id": qm_id,
                        "qm_name": info["name"],
                        "metric": metric,
                        "current_utilization": round(current_util, 4),
                        "predicted_breach_date": now.isoformat(),
                        "threshold": threshold_pct,
                        "max_capacity": max_cap,
                        "recommended_action": (
                            f"Immediate action required: {metric} already at "
                            f"{current_util:.0%} of capacity on QM {info['name']}. "
                            f"Consider scaling or redistributing workload."
                        ),
                    })
                    continue

                # Positive slope → predict breach
                if slope > 0:
                    days_to_breach = (threshold_val - current_val) / slope
                    if 0 < days_to_breach <= horizon_days:
                        breach_date = now + timedelta(days=days_to_breach)
                        alerts.append({
                            "qm_id": qm_id,
                            "qm_name": info["name"],
                            "metric": metric,
                            "current_utilization": round(current_util, 4),
                            "predicted_breach_date": breach_date.isoformat(),
                            "threshold": threshold_pct,
                            "max_capacity": max_cap,
                            "recommended_action": (
                                f"Projected {metric} breach on QM {info['name']} "
                                f"in ~{days_to_breach:.0f} days. Consider proactive "
                                f"scaling or workload redistribution."
                            ),
                        })

        return alerts

    # -----------------------------------------------------------------
    # Onboarding impact simulation  (Req 23.4)
    # -----------------------------------------------------------------

    def simulate_onboarding_impact(
        self,
        onboarding_request: dict,
        current_topology: TopologySnapshotEvent,
    ) -> dict:
        """Forecast impact of onboarding a new application.

        ``onboarding_request`` should contain:
        - ``app_name``: name of the new application
        - ``connected_qm_id``: QM the app will connect to
        - ``role``: "producer" | "consumer" | "both"
        - ``produces_to``: list of queue IDs (optional)
        - ``consumes_from``: list of queue IDs (optional)
        - ``new_queues``: int — number of new queues to create (optional)
        - ``new_channels``: int — number of new channels to create (optional)

        Returns a dict with before/after counts and projected complexity
        change.
        """
        before = {
            "qm_count": len(current_topology.queue_managers),
            "queue_count": len(current_topology.queues),
            "channel_count": len(current_topology.channels),
            "app_count": len(current_topology.applications),
        }

        new_queues = onboarding_request.get("new_queues", 0)
        new_channels = onboarding_request.get("new_channels", 0)

        after = {
            "qm_count": before["qm_count"],  # onboarding doesn't add QMs
            "queue_count": before["queue_count"] + new_queues,
            "channel_count": before["channel_count"] + new_channels,
            "app_count": before["app_count"] + 1,
        }

        # Check capacity on the target QM
        target_qm_id = onboarding_request.get("connected_qm_id", "")
        capacity_warning = None
        for qm in current_topology.queue_managers:
            if qm.id == target_qm_id:
                projected_queues = sum(
                    1 for q in current_topology.queues if q.owning_qm_id == qm.id
                ) + new_queues
                projected_channels = sum(
                    1 for ch in current_topology.channels if ch.from_qm_id == qm.id
                ) + new_channels

                if projected_queues > qm.max_queues * 0.8:
                    capacity_warning = (
                        f"QM {qm.name} will be at {projected_queues}/{qm.max_queues} "
                        f"queues ({projected_queues / qm.max_queues:.0%}) after onboarding."
                    )
                elif projected_channels > qm.max_channels * 0.8:
                    capacity_warning = (
                        f"QM {qm.name} will be at {projected_channels}/{qm.max_channels} "
                        f"channels ({projected_channels / qm.max_channels:.0%}) after onboarding."
                    )
                break

        delta = {
            k: after[k] - before[k] for k in before
        }

        return {
            "app_name": onboarding_request.get("app_name", "unknown"),
            "connected_qm_id": target_qm_id,
            "before": before,
            "after": after,
            "delta": delta,
            "capacity_warning": capacity_warning,
        }

    # -----------------------------------------------------------------
    # Capacity report generation  (Req 23.5)
    # -----------------------------------------------------------------

    def generate_capacity_report(
        self,
        snapshots: list[TopologySnapshotEvent],
        current_topology: TopologySnapshotEvent,
        threshold_pct: float = 0.8,
    ) -> dict:
        """Generate a capacity report with current utilization and projections.

        Returns a dict with:
        - ``report_id``
        - ``generated_at`` (ISO timestamp)
        - ``qm_utilization``: per-QM current utilization
        - ``projections``: per-QM projected utilization at 30/60/90 days
        - ``alerts``: capacity alerts for QMs approaching thresholds
        - ``recommended_actions``: list of recommended scaling actions
        """
        report_id = str(uuid.uuid4())
        generated_at = datetime.now(timezone.utc)

        # Current utilization per QM
        qm_utilization: list[dict] = []
        for qm in current_topology.queue_managers:
            queue_count = sum(
                1 for q in current_topology.queues if q.owning_qm_id == qm.id
            )
            channel_count = sum(
                1 for ch in current_topology.channels if ch.from_qm_id == qm.id
            )
            qm_utilization.append({
                "qm_id": qm.id,
                "qm_name": qm.name,
                "queue_count": queue_count,
                "max_queues": qm.max_queues,
                "queue_utilization": round(
                    queue_count / qm.max_queues if qm.max_queues > 0 else 0.0, 4
                ),
                "channel_count": channel_count,
                "max_channels": qm.max_channels,
                "channel_utilization": round(
                    channel_count / qm.max_channels if qm.max_channels > 0 else 0.0, 4
                ),
            })

        # Projections at 30/60/90 days using linear regression on history
        projections: list[dict] = []
        if len(snapshots) >= 2:
            snapshots_sorted = sorted(snapshots, key=lambda s: s.timestamp)
            t0 = snapshots_sorted[0].timestamp

            # Build per-QM time series
            qm_queue_ts: dict[str, list[tuple[float, float]]] = {}
            qm_channel_ts: dict[str, list[tuple[float, float]]] = {}

            for snap in snapshots_sorted:
                day_offset = (snap.timestamp - t0).total_seconds() / 86400.0
                queues_per_qm: dict[str, int] = {}
                channels_per_qm: dict[str, int] = {}
                for q in snap.queues:
                    queues_per_qm[q.owning_qm_id] = queues_per_qm.get(q.owning_qm_id, 0) + 1
                for ch in snap.channels:
                    channels_per_qm[ch.from_qm_id] = channels_per_qm.get(ch.from_qm_id, 0) + 1

                for qm in snap.queue_managers:
                    qm_queue_ts.setdefault(qm.id, []).append(
                        (day_offset, float(queues_per_qm.get(qm.id, 0)))
                    )
                    qm_channel_ts.setdefault(qm.id, []).append(
                        (day_offset, float(channels_per_qm.get(qm.id, 0)))
                    )

            now_offset = (generated_at - t0).total_seconds() / 86400.0

            for qm in current_topology.queue_managers:
                qm_proj: dict[str, Any] = {
                    "qm_id": qm.id,
                    "qm_name": qm.name,
                }
                for horizon in [30, 60, 90]:
                    future_offset = now_offset + horizon
                    # Queue projection
                    q_ts = qm_queue_ts.get(qm.id, [])
                    if q_ts:
                        slope, intercept = _linear_regression(
                            [p[0] for p in q_ts], [p[1] for p in q_ts]
                        )
                        proj_queues = max(0, _predict(slope, intercept, future_offset))
                    else:
                        proj_queues = 0.0

                    # Channel projection
                    c_ts = qm_channel_ts.get(qm.id, [])
                    if c_ts:
                        slope, intercept = _linear_regression(
                            [p[0] for p in c_ts], [p[1] for p in c_ts]
                        )
                        proj_channels = max(0, _predict(slope, intercept, future_offset))
                    else:
                        proj_channels = 0.0

                    qm_proj[f"{horizon}d_queue_utilization"] = round(
                        proj_queues / qm.max_queues if qm.max_queues > 0 else 0.0, 4
                    )
                    qm_proj[f"{horizon}d_channel_utilization"] = round(
                        proj_channels / qm.max_channels if qm.max_channels > 0 else 0.0, 4
                    )

                projections.append(qm_proj)

        # Alerts for QMs approaching thresholds
        alerts = self.forecast_capacity(
            snapshots, horizon_days=90, threshold_pct=threshold_pct
        )

        # Recommended actions
        recommended_actions: list[str] = []
        for alert in alerts:
            recommended_actions.append(alert["recommended_action"])

        if not recommended_actions:
            recommended_actions.append(
                "All queue managers are within capacity thresholds. "
                "No immediate scaling actions required."
            )

        return {
            "report_id": report_id,
            "generated_at": generated_at.isoformat(),
            "qm_utilization": qm_utilization,
            "projections": projections,
            "alerts": alerts,
            "recommended_actions": recommended_actions,
        }
