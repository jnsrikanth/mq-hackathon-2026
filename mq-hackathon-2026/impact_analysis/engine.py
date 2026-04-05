"""Impact Analysis Engine for the MQ Guardian Platform.

Computes blast radius, risk scores, downstream dependencies, rollback
plans, and change simulation for topology change proposals.  All logic
is pure-Python — no external services required.

Requirements: 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from graph_store.neo4j_client import Neo4jGraphStore
from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    RiskScore,
    TopologySnapshotEvent,
)
from policy_engine.engine import PolicyEngine

logger = logging.getLogger(__name__)


class ImpactAnalysisEngine:
    """Computes blast radius, risk score, dependencies, and rollback plans.

    All methods are stateless — they accept the graph store (and optionally
    the policy engine) as arguments so the caller controls the lifecycle.
    """

    # -----------------------------------------------------------------
    # Blast radius  (Req 22.1)
    # -----------------------------------------------------------------

    def compute_blast_radius(
        self,
        change_proposal: dict,
        graph_store: Neo4jGraphStore,
    ) -> dict:
        """Traverse the graph for direct and transitive affected objects.

        ``change_proposal`` must contain an ``object_ids`` list of MQ
        object IDs that are the *seed* of the change.

        Returns a dict with ``direct`` and ``transitive`` lists, each
        containing ``{"id", "label", "name"}`` dicts, plus a
        ``total_affected`` count.
        """
        result = graph_store.query_blast_radius(change_proposal)
        direct: list[dict] = result.get("direct", [])
        transitive: list[dict] = result.get("transitive", [])
        return {
            "direct": direct,
            "transitive": transitive,
            "total_affected": len(direct) + len(transitive),
        }

    # -----------------------------------------------------------------
    # Risk scoring  (Req 22.2)
    # -----------------------------------------------------------------

    def compute_risk_score(
        self,
        blast_radius: dict,
        app_criticality: dict[str, str] | None = None,
        historical_outcomes: list[dict] | None = None,
    ) -> str:
        """Estimate risk as low / medium / high / critical.

        Factors:
        1. Total affected object count.
        2. Criticality of affected applications (``app_criticality`` maps
           app-id → "low" | "medium" | "high" | "critical").
        3. Historical change outcomes — ratio of past failures.

        Returns one of the ``RiskScore`` enum values as a plain string.
        """
        app_criticality = app_criticality or {}
        historical_outcomes = historical_outcomes or []

        total = blast_radius.get("total_affected", 0)

        # --- Factor 1: affected count score (0-3) ---
        if total == 0:
            count_score = 0
        elif total <= 5:
            count_score = 1
        elif total <= 15:
            count_score = 2
        else:
            count_score = 3

        # --- Factor 2: max criticality of affected apps (0-3) ---
        crit_levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        all_affected_ids = {
            obj["id"]
            for obj in blast_radius.get("direct", []) + blast_radius.get("transitive", [])
        }
        max_crit = 0
        for aid, crit in app_criticality.items():
            if aid in all_affected_ids:
                max_crit = max(max_crit, crit_levels.get(crit, 0))

        # --- Factor 3: historical failure ratio (0-3) ---
        hist_score = 0
        if historical_outcomes:
            failures = sum(
                1 for o in historical_outcomes if o.get("outcome") == "failure"
            )
            ratio = failures / len(historical_outcomes)
            if ratio >= 0.5:
                hist_score = 3
            elif ratio >= 0.25:
                hist_score = 2
            elif ratio > 0:
                hist_score = 1

        composite = count_score + max_crit + hist_score

        if composite >= 7:
            return RiskScore.CRITICAL.value
        if composite >= 5:
            return RiskScore.HIGH.value
        if composite >= 3:
            return RiskScore.MEDIUM.value
        return RiskScore.LOW.value

    # -----------------------------------------------------------------
    # Downstream dependencies  (Req 22.3)
    # -----------------------------------------------------------------

    def identify_downstream_dependencies(
        self,
        change_proposal: dict,
        graph_store: Neo4jGraphStore,
    ) -> dict:
        """Find downstream consumers, channels, and transmission queues.

        Delegates to ``Neo4jGraphStore.query_downstream_dependencies``
        using the ``object_ids`` from the change proposal.

        Returns ``{"consumers": [...], "channels": [...], "xmitqs": [...]}``.
        """
        object_ids: list[str] = change_proposal.get("object_ids", [])
        return graph_store.query_downstream_dependencies(object_ids)

    # -----------------------------------------------------------------
    # Rollback plan  (Req 22.4)
    # -----------------------------------------------------------------

    def generate_rollback_plan(
        self,
        change_proposal: dict,
        current_topology: TopologySnapshotEvent,
    ) -> dict:
        """Generate exact reversal actions to restore the pre-change state.

        For each action in ``change_proposal["actions"]`` the engine
        produces the inverse operation using the *current* topology as
        the reference for original values.

        Returns a dict with ``rollback_id``, ``actions`` list, and
        ``original_state`` snapshot summary.
        """
        actions: list[dict] = change_proposal.get("actions", [])
        rollback_actions: list[dict] = []

        # Build lookup maps from the current topology
        qm_by_id = {qm.id: qm for qm in current_topology.queue_managers}
        queue_by_id = {q.id: q for q in current_topology.queues}
        channel_by_id = {ch.id: ch for ch in current_topology.channels}
        app_by_id = {a.id: a for a in current_topology.applications}

        for action in actions:
            action_type = action.get("type", "")
            target_id = action.get("target_id", "")
            obj_type = action.get("object_type", "")

            if action_type == "delete":
                # Rollback = re-create the deleted object
                original = (
                    qm_by_id.get(target_id)
                    or queue_by_id.get(target_id)
                    or channel_by_id.get(target_id)
                    or app_by_id.get(target_id)
                )
                rollback_actions.append({
                    "type": "create",
                    "object_type": obj_type,
                    "target_id": target_id,
                    "restore_data": original.model_dump() if original else {},
                })

            elif action_type == "create":
                # Rollback = delete the newly created object
                rollback_actions.append({
                    "type": "delete",
                    "object_type": obj_type,
                    "target_id": target_id,
                })

            elif action_type == "update":
                # Rollback = restore original properties
                original = (
                    qm_by_id.get(target_id)
                    or queue_by_id.get(target_id)
                    or channel_by_id.get(target_id)
                    or app_by_id.get(target_id)
                )
                rollback_actions.append({
                    "type": "update",
                    "object_type": obj_type,
                    "target_id": target_id,
                    "restore_data": original.model_dump() if original else {},
                })

            else:
                # Unknown action — record a no-op rollback entry
                rollback_actions.append({
                    "type": "noop",
                    "object_type": obj_type,
                    "target_id": target_id,
                    "reason": f"Unknown action type '{action_type}'",
                })

        return {
            "rollback_id": str(uuid.uuid4()),
            "actions": rollback_actions,
            "original_state": {
                "snapshot_id": current_topology.snapshot_id,
                "timestamp": current_topology.timestamp.isoformat(),
                "qm_count": len(current_topology.queue_managers),
                "queue_count": len(current_topology.queues),
                "channel_count": len(current_topology.channels),
                "app_count": len(current_topology.applications),
            },
        }

    # -----------------------------------------------------------------
    # Change simulation  (Req 22.5)
    # -----------------------------------------------------------------

    def simulate_change(
        self,
        change_proposal: dict,
        graph_store: Neo4jGraphStore,
        policy_engine: PolicyEngine,
    ) -> dict:
        """Simulate a change against the graph model without modifying production.

        Steps:
        1. Create a sandbox copy of the current topology.
        2. Apply the proposed actions to the sandbox.
        3. Build a ``TopologySnapshotEvent`` from the sandbox state.
        4. Validate the post-change topology against the policy engine.

        Returns ``{"valid": bool, "violations": [...], "post_change_topology_summary": {...}}``.
        """
        # 1. Sandbox copy
        sandbox = graph_store.create_sandbox_copy(session_id=str(uuid.uuid4()))
        sandbox_nodes: dict[tuple[str, str], dict] = sandbox["nodes"]
        sandbox_edges: dict = sandbox["edges"]

        # 2. Apply proposed actions
        actions: list[dict] = change_proposal.get("actions", [])
        for action in actions:
            self._apply_sandbox_action(action, sandbox_nodes, sandbox_edges)

        # 3. Build topology snapshot from sandbox
        post_topology = self._build_topology_from_sandbox(sandbox_nodes)

        # 4. Validate against policy engine
        violations = policy_engine.validate_mq_constraints(post_topology)

        return {
            "valid": len(violations) == 0,
            "violations": [
                {
                    "policy_id": v.policy_id,
                    "policy_name": v.policy_name,
                    "description": v.description,
                    "offending_objects": v.offending_objects,
                }
                for v in violations
            ],
            "post_change_topology_summary": {
                "qm_count": len(post_topology.queue_managers),
                "queue_count": len(post_topology.queues),
                "channel_count": len(post_topology.channels),
                "app_count": len(post_topology.applications),
            },
        }

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _apply_sandbox_action(
        action: dict,
        nodes: dict[tuple[str, str], dict],
        edges: dict,
    ) -> None:
        """Apply a single action to the sandbox node/edge dicts."""
        action_type = action.get("type", "")
        target_id = action.get("target_id", "")
        obj_type = action.get("object_type", "")

        label = _object_type_to_label(obj_type)

        if action_type == "delete":
            key = (label, target_id)
            nodes.pop(key, None)
            edges.pop(key, None)
            # Remove incoming edges pointing to this node
            for src_key in list(edges.keys()):
                edges[src_key] = [
                    e for e in edges[src_key]
                    if not (e[1] == label and e[2] == target_id)
                ]

        elif action_type == "create":
            data = action.get("data", {})
            key = (label, target_id)
            nodes[key] = {"id": target_id, **data}

        elif action_type == "update":
            key = (label, target_id)
            if key in nodes:
                updates = action.get("data", {})
                nodes[key].update(updates)

    @staticmethod
    def _build_topology_from_sandbox(
        nodes: dict[tuple[str, str], dict],
    ) -> TopologySnapshotEvent:
        """Reconstruct a ``TopologySnapshotEvent`` from sandbox node data."""
        qms: list[QueueManager] = []
        queues: list[Queue] = []
        channels: list[Channel] = []
        apps: list[Application] = []

        for (label, nid), props in nodes.items():
            if label == "QueueManager":
                qms.append(QueueManager(
                    id=props.get("id", nid),
                    name=props.get("name", ""),
                    hostname=props.get("hostname", ""),
                    port=props.get("port", 0),
                    neighborhood=props.get("neighborhood"),
                    region=props.get("region"),
                    lob=props.get("lob"),
                ))
            elif label == "Queue":
                qt_raw = props.get("queue_type", "local")
                queues.append(Queue(
                    id=props.get("id", nid),
                    name=props.get("name", ""),
                    queue_type=QueueType(qt_raw) if qt_raw else QueueType.LOCAL,
                    owning_qm_id=props.get("owning_qm_id", ""),
                    target_qm_id=props.get("target_qm_id"),
                    xmitq_id=props.get("xmitq_id"),
                    lob=props.get("lob"),
                ))
            elif label == "Channel":
                dir_raw = props.get("direction", "sender")
                channels.append(Channel(
                    id=props.get("id", nid),
                    name=props.get("name", ""),
                    direction=ChannelDirection(dir_raw) if dir_raw else ChannelDirection.SENDER,
                    from_qm_id=props.get("from_qm_id", ""),
                    to_qm_id=props.get("to_qm_id", ""),
                    lob=props.get("lob"),
                ))
            elif label == "Application":
                role = props.get("role", "producer")
                apps.append(Application(
                    id=props.get("id", nid),
                    name=props.get("name", ""),
                    connected_qm_id=props.get("connected_qm_id", ""),
                    role=role if role in ("producer", "consumer", "both") else "producer",
                    produces_to=props.get("produces_to", []),
                    consumes_from=props.get("consumes_from", []),
                    criticality=props.get("criticality"),
                    lob=props.get("lob"),
                ))

        return TopologySnapshotEvent(
            snapshot_id="sandbox-simulation",
            correlation_id="",
            timestamp=datetime.now(timezone.utc),
            mode="bootstrap",
            queue_managers=qms,
            queues=queues,
            channels=channels,
            applications=apps,
        )


# =====================================================================
# Module-level helpers
# =====================================================================

_LABEL_MAP: dict[str, str] = {
    "queue_manager": "QueueManager",
    "qm": "QueueManager",
    "queue": "Queue",
    "channel": "Channel",
    "application": "Application",
    "app": "Application",
}


def _object_type_to_label(obj_type: str) -> str:
    """Map a human-friendly object type string to a graph label."""
    return _LABEL_MAP.get(obj_type.lower(), obj_type)
