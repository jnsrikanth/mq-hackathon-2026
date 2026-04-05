"""Steady-State Agent — continuous topology monitoring and drift detection.

After Bootstrap completes and the operator accepts the target topology,
the Agent enters Steady-State mode:

  1. Consumes topology snapshot events from the Event Bus
  2. Compares each snapshot against the frozen target in the Graph Store
  3. Detects drift (configuration, structural, policy violations)
  4. Proposes remediations via the Decision Engine
  5. Routes proposals through HiTL approval
  6. On approval, generates IaC artifacts and updates the target

Also handles:
  - New application onboarding requests
  - Queue/channel creation proposals
  - Continuous constraint validation

The Graph Store is embedded — all queries run in-process.
The Event Bus is the in-memory Python implementation (swappable to Kafka).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from event_bus.producer import EventBusProducer
from event_bus.consumer import EventBusConsumer
from event_bus.schemas import KAFKA_TOPICS
from graph_store.neo4j_client import Neo4jGraphStore
from policy_engine.engine import PolicyEngine
from decision_engine.engine import ExplainableDecisionEngine
from impact_analysis.engine import ImpactAnalysisEngine
from transformer.validators import normalize_app, qm_for, build_edges_target, validate_edges_target
from agent_brain.llm_config import get_llm

logger = logging.getLogger(__name__)


class SteadyStateAgent:
    """Continuous monitoring agent for steady-state topology management.

    Embeds:
      - Graph Store (in-memory, Neo4j-compatible)
      - Event Bus producer/consumer (in-memory, Kafka-compatible)
      - Policy Engine, Decision Engine, Impact Analysis Engine
      - LLM connection (Ollama locally, Tachyon in production)
    """

    def __init__(
        self,
        graph_store: Neo4jGraphStore | None = None,
        producer: EventBusProducer | None = None,
        consumer: EventBusConsumer | None = None,
        llm: Any = None,
    ) -> None:
        self.graph_store = graph_store or Neo4jGraphStore()
        self.producer = producer or EventBusProducer()
        self.consumer = consumer or EventBusConsumer()
        self.policy_engine = PolicyEngine()
        self.decision_engine = ExplainableDecisionEngine()
        self.impact_engine = ImpactAnalysisEngine()
        self.llm = llm or get_llm()

        # State
        self.target_frozen = False
        self.target_version: str | None = None
        self.pending_proposals: list[dict] = []
        self.approved_changes: list[dict] = []
        self.rejected_changes: list[dict] = []
        self.drift_events: list[dict] = []
        self.onboarding_requests: list[dict] = []
        self.audit_trail: list[dict] = []

    # ------------------------------------------------------------------
    # Bootstrap acceptance
    # ------------------------------------------------------------------

    def accept_bootstrap(self, version_tag: str) -> dict:
        """Freeze the target topology after operator acceptance.

        This is the transition from Bootstrap → Steady-State.
        After freezing, the IaC pipeline generates Terraform + Ansible artifacts.
        """
        self.target_frozen = True
        self.target_version = version_tag

        # Run IaC pipeline to generate infrastructure artifacts
        iac_result = None
        try:
            from iac_engine.pipeline import IaCPipeline
            pipeline = IaCPipeline(
                artifacts_dir=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "artifacts",
                )
            )
            # Build topology dict from graph store edges
            topo = self._build_topology_dict()
            iac_result = pipeline.execute_full_pipeline(topo, environment="prod")
        except Exception as e:
            logger.warning("IaC pipeline failed: %s", e)
            iac_result = {"status": "failed", "error": str(e)}

        event = self._create_event(
            "bootstrap_accepted",
            {
                "version_tag": version_tag,
                "decision": "ACCEPT",
                "actor": "operator",
                "message": "Target topology accepted and frozen as baseline.",
                "iac_pipeline": iac_result,
            },
        )
        self._publish_audit(event)
        self._publish_event("mq-human-feedback", event)

        logger.info("Bootstrap accepted — target frozen at version %s", version_tag)
        return {"event": event, "iac_pipeline": iac_result}

    def _build_topology_dict(self) -> dict:
        """Build a topology dict from the graph store for IaC generation."""
        edges = []
        channels = []
        seen_channels = set()

        for (from_label, from_id), edge_list in self.graph_store._edges.items():
            for rel_type, to_label, to_id, _ in edge_list:
                edges.append({"src": from_id, "dst": to_id, "relation": rel_type})
                if rel_type == "transit_via_channel_pair" or (
                    from_id.startswith("QM_") and to_id.startswith("QM_")
                ):
                    pair_key = (from_id, to_id)
                    if pair_key not in seen_channels:
                        seen_channels.add(pair_key)
                        channels.append({
                            "from_qm": from_id,
                            "to_qm": to_id,
                            "sender_channel": f"CH.{from_id}.to.{to_id}",
                            "receiver_channel": f"CH.{to_id}.from.{from_id}",
                        })

        # Also try to read channels from the exports CSV
        channels_csv = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "artifacts", "exports", "channels.csv",
        )
        if os.path.exists(channels_csv) and not channels:
            import csv
            with open(channels_csv, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    channels.append(dict(row))

        return {"edges": edges, "channels": channels}

    def reject_bootstrap(self, version_tag: str, reason: str = "") -> dict:
        """Reject the bootstrap proposal."""
        event = self._create_event(
            "bootstrap_rejected",
            {
                "version_tag": version_tag,
                "decision": "REJECT",
                "actor": "operator",
                "reason": reason,
            },
        )
        self._publish_audit(event)
        self._publish_event("mq-human-feedback", event)

        logger.info("Bootstrap rejected — version %s", version_tag)
        return event

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def detect_drift(self, live_snapshot: dict) -> list[dict]:
        """Compare a live topology snapshot against the frozen target.

        Returns a list of drift events, each containing:
          - drift_type: configuration_drift | structural_drift | policy_drift
          - affected_objects: list of object IDs
          - description: human-readable explanation
          - proposed_remediation: suggested fix
        """
        if not self.target_frozen:
            return []

        drifts: list[dict] = []
        target = self.graph_store.get_current_topology()

        # Build lookup sets from target
        target_qm_ids = {qm.id for qm in target.queue_managers}
        target_queue_ids = {q.id for q in target.queues}
        target_app_ids = {a.id for a in target.applications}
        target_channel_ids = {ch.id for ch in target.channels}

        live_qm_ids = set(live_snapshot.get("queue_manager_ids", []))
        live_queue_ids = set(live_snapshot.get("queue_ids", []))
        live_app_ids = set(live_snapshot.get("app_ids", []))

        # Structural drift: new objects not in target
        new_qms = live_qm_ids - target_qm_ids
        if new_qms:
            drifts.append({
                "drift_type": "structural_drift",
                "severity": "warning",
                "affected_objects": list(new_qms),
                "description": f"{len(new_qms)} new queue manager(s) detected not in target: {list(new_qms)[:5]}",
                "proposed_remediation": "Validate new QMs against policy and add to target if compliant.",
            })

        new_queues = live_queue_ids - target_queue_ids
        if new_queues:
            drifts.append({
                "drift_type": "structural_drift",
                "severity": "warning",
                "affected_objects": list(new_queues),
                "description": f"{len(new_queues)} new queue(s) detected not in target.",
                "proposed_remediation": "Review new queues and update target topology.",
            })

        # Missing objects: in target but not in live
        missing_qms = target_qm_ids - live_qm_ids
        if missing_qms:
            drifts.append({
                "drift_type": "structural_drift",
                "severity": "critical",
                "affected_objects": list(missing_qms),
                "description": f"{len(missing_qms)} target queue manager(s) missing from live topology.",
                "proposed_remediation": "Investigate missing QMs — possible infrastructure failure.",
            })

        # Policy drift: run constraint checks
        constraint_result = self.graph_store.check_constraints()
        if not constraint_result["ok"]:
            for err in constraint_result["errors"]:
                drifts.append({
                    "drift_type": "policy_drift",
                    "severity": "critical",
                    "affected_objects": constraint_result.get("sample_violations", []),
                    "description": err,
                    "proposed_remediation": "Re-run transformation to restore compliance.",
                })

        # Store and publish drift events
        for drift in drifts:
            drift["correlation_id"] = str(uuid.uuid4())
            drift["timestamp"] = datetime.now(timezone.utc).isoformat()
            self.drift_events.append(drift)
            self._publish_event("mq-anomaly-detected", self._create_event("drift_detected", drift))

        logger.info("Drift detection: %d drift(s) found", len(drifts))
        return drifts

    # ------------------------------------------------------------------
    # Application onboarding
    # ------------------------------------------------------------------

    def onboard_application(self, request: dict) -> dict:
        """Process a new application onboarding request.

        Steps:
          1. Determine required MQ objects (QM, queues, channels)
          2. Validate against policy engine
          3. Compute impact analysis
          4. Generate proposal for HiTL approval

        The QM is deterministically assigned using qm_for(app_id).
        This ensures 1-QM-per-App by construction.
        If the QM already exists in the target topology, the app is added to it.
        If not, a NEW QM is provisioned via Terraform.
        """
        app_name = request.get("app_name", "")
        # Use explicit app_id if provided, otherwise derive from name
        raw_app_id = request.get("app_id", "") or app_name
        app_id = normalize_app(raw_app_id)
        qm_id = qm_for(app_id)
        role = request.get("role", "producer")
        communicates_with = request.get("communicates_with", [])
        lob = request.get("line_of_business", "")
        neighborhood = request.get("neighborhood", "")

        # Check if QM already exists in graph store
        existing_qm = self.graph_store._get_node("QueueManager", qm_id)
        qm_is_new = existing_qm is None

        # Determine required objects
        new_objects = {
            "queue_manager": qm_id,
            "queue_manager_is_new": qm_is_new,
            "app_id": app_id,
            "app_name": app_name,
            "role": role,
            "line_of_business": lob,
            "neighborhood": neighborhood,
            "queues": [],
            "channels": [],
        }

        # For each communication partner, create the full MQ routing chain
        seen_queues: set[str] = set()
        for partner in communicates_with:
            partner_id = normalize_app(partner)
            partner_qm = qm_for(partner_id)

            if role in ("producer", "both"):
                # Producer side: QREMOTE on app's QM → routes to partner's QM
                rq_name = f"RQ.{app_id}.TO.{partner_id}"
                if rq_name not in seen_queues:
                    new_objects["queues"].append({
                        "name": rq_name,
                        "type": "QREMOTE",
                        "owning_qm": qm_id,
                        "target_qm": partner_qm,
                        "remote_q_name": f"LQ.{partner_id}",
                    })
                    seen_queues.add(rq_name)

                # Transmission queue on app's QM
                xq_name = f"XQ.{partner_qm}"
                if xq_name not in seen_queues:
                    new_objects["queues"].append({
                        "name": xq_name,
                        "type": "TRANSMISSION",
                        "owning_qm": qm_id,
                    })
                    seen_queues.add(xq_name)

                # Channel pair between the two QMs
                new_objects["channels"].append({
                    "from_qm": qm_id,
                    "to_qm": partner_qm,
                    "sender": f"CH.{qm_id}.to.{partner_qm}",
                    "receiver": f"CH.{partner_qm}.from.{qm_id}",
                })

            if role in ("consumer", "both"):
                # Consumer side: QLOCAL on app's QM
                lq_name = f"LQ.{app_id}"
                if lq_name not in seen_queues:
                    new_objects["queues"].append({
                        "name": lq_name,
                        "type": "QLOCAL",
                        "owning_qm": qm_id,
                    })
                    seen_queues.add(lq_name)

        # Validate the new edges
        intents = [(app_id, normalize_app(p)) for p in communicates_with]
        edges = build_edges_target(intents)
        ok, errors, _ = validate_edges_target(edges)

        # Impact analysis
        blast = self.impact_engine.compute_blast_radius(
            {"object_ids": [qm_id]}, self.graph_store
        )
        risk = self.impact_engine.compute_risk_score(blast)

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "type": "onboarding",
            "app_name": app_name,
            "app_id": app_id,
            "assigned_qm": qm_id,
            "qm_is_new": qm_is_new,
            "new_objects": new_objects,
            "validation_passed": ok,
            "validation_errors": errors,
            "blast_radius": blast,
            "risk_score": risk,
            "status": "pending_approval",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.pending_proposals.append(proposal)
        self.onboarding_requests.append(request)

        # Publish to event bus
        self._publish_event("mq-approval-needed", self._create_event("onboarding_proposal", proposal))
        self._publish_audit(self._create_event("onboarding_requested", {
            "app_name": app_name, "app_id": app_id, "proposal_id": proposal["proposal_id"],
        }))

        logger.info("Onboarding proposal created for %s (%s): %s", app_name, app_id, proposal["proposal_id"])
        return proposal

    # ------------------------------------------------------------------
    # HiTL approval handling
    # ------------------------------------------------------------------

    def approve_proposal(self, proposal_id: str, actor: str = "operator") -> dict:
        """Approve a pending proposal."""
        proposal = self._find_proposal(proposal_id)
        if not proposal:
            return {"error": f"Proposal {proposal_id} not found"}

        proposal["status"] = "approved"
        proposal["approved_by"] = actor
        proposal["approved_at"] = datetime.now(timezone.utc).isoformat()
        self.approved_changes.append(proposal)

        self._publish_event("mq-human-feedback", self._create_event("proposal_approved", {
            "proposal_id": proposal_id, "actor": actor,
        }))
        self._publish_audit(self._create_event("proposal_approved", {
            "proposal_id": proposal_id, "actor": actor,
        }))

        return proposal

    def reject_proposal(self, proposal_id: str, actor: str = "operator", reason: str = "") -> dict:
        """Reject a pending proposal."""
        proposal = self._find_proposal(proposal_id)
        if not proposal:
            return {"error": f"Proposal {proposal_id} not found"}

        proposal["status"] = "rejected"
        proposal["rejected_by"] = actor
        proposal["rejection_reason"] = reason
        proposal["rejected_at"] = datetime.now(timezone.utc).isoformat()
        self.rejected_changes.append(proposal)

        self._publish_event("mq-human-feedback", self._create_event("proposal_rejected", {
            "proposal_id": proposal_id, "actor": actor, "reason": reason,
        }))
        self._publish_audit(self._create_event("proposal_rejected", {
            "proposal_id": proposal_id, "actor": actor, "reason": reason,
        }))

        return proposal

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return the current agent status for the Web UI."""
        return {
            "mode": "steady_state" if self.target_frozen else "bootstrap",
            "target_frozen": self.target_frozen,
            "target_version": self.target_version,
            "pending_proposals": len(self.pending_proposals),
            "approved_changes": len(self.approved_changes),
            "rejected_changes": len(self.rejected_changes),
            "drift_events": len(self.drift_events),
            "onboarding_requests": len(self.onboarding_requests),
            "audit_trail_size": len(self.audit_trail),
            "graph_store": {
                "nodes": len(self.graph_store._nodes),
                "edges": sum(len(e) for e in self.graph_store._edges.values()),
                "versions": list(self.graph_store._versions.keys()),
            },
            "event_bus_topics": list(KAFKA_TOPICS.keys()),
        }

    def get_drift_events(self) -> list[dict]:
        return list(self.drift_events)

    def get_pending_proposals(self) -> list[dict]:
        return [p for p in self.pending_proposals if p["status"] == "pending_approval"]

    def get_audit_trail(self) -> list[dict]:
        return list(self.audit_trail)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_proposal(self, proposal_id: str) -> dict | None:
        for p in self.pending_proposals:
            if p.get("proposal_id") == proposal_id:
                return p
        return None

    def _create_event(self, event_type: str, payload: dict) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "correlation_id": payload.get("correlation_id", str(uuid.uuid4())),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }

    def _publish_event(self, topic: str, event: dict) -> None:
        self.producer.publish(topic, event)

    def _publish_audit(self, event: dict) -> None:
        self.audit_trail.append(event)
        self.producer.publish("mq-audit-log", event)
        self.graph_store.persist_audit_event(event.get("payload", event))
