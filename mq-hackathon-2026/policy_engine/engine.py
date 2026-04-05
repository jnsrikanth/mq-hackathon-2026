"""Sentinel + OPA policy enforcement for MQ topology validation.

Implements pure-Python policy evaluation logic that mirrors what Sentinel
and OPA would enforce in a production environment.  No external policy
servers are required — all rules are evaluated natively.

Policy categories:
  * MQ Constraint Policies (Sentinel-style): structural topology rules
  * OPA Policies: access-control and data-validation rules
  * LOB Policies: line-of-business-specific governance rules

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 9.1, 9.2, 9.3, 9.4, 26.2
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)


@dataclass
class PolicyViolation:
    """A single policy violation detected during evaluation.

    Attributes:
        policy_id: Machine-readable identifier (e.g. ``exactly_one_qm_per_app``).
        policy_name: Human-readable policy name.
        description: Detailed description of the violation.
        offending_objects: List of object IDs that caused the violation.
        remediation_suggestion: Actionable suggestion to fix the violation.
    """

    policy_id: str
    policy_name: str
    description: str
    offending_objects: list[str] = field(default_factory=list)
    remediation_suggestion: str = ""


class PolicyEngine:
    """Evaluates Sentinel and OPA policies against topology changes.

    All evaluation is pure-Python — no external Sentinel or OPA servers.
    """

    # MQ Constraint Policies (Sentinel-style)
    MQ_POLICIES: list[str] = [
        "exactly_one_qm_per_app",
        "producers_write_to_remote_queue_on_own_qm",
        "remote_queue_uses_xmitq_via_channel",
        "deterministic_channel_naming",
        "consumers_read_from_local_queue_on_own_qm",
        "secure_by_default",
        "neighbourhood_aligned",
    ]

    # OPA Policies
    OPA_POLICIES: list[str] = [
        "access_control_role_based",
        "data_validation_required_fields",
        "change_scope_limit",
    ]

    # ------------------------------------------------------------------
    # MQ constraint validation  (Req 3.1-3.6)
    # ------------------------------------------------------------------

    def validate_mq_constraints(
        self, topology: TopologySnapshotEvent,
    ) -> list[PolicyViolation]:
        """Validate full topology against all MQ constraints.

        Checks:
          1. 1 QM per app  (Req 3.1)
          2. Producers → remote queue on own QM  (Req 3.2)
          3. Remote queue → xmitq → channel  (Req 3.3)
          4. Deterministic channel naming  (Req 3.4)
          5. Consumers → local queue on own QM  (Req 3.5)

        Returns a list of PolicyViolation objects (empty means compliant).
        """
        violations: list[PolicyViolation] = []

        # Build lookup maps
        qm_ids = {qm.id for qm in topology.queue_managers}
        queue_by_id: dict[str, Queue] = {q.id: q for q in topology.queues}
        app_by_id: dict[str, Application] = {a.id: a for a in topology.applications}
        channel_by_id: dict[str, Channel] = {ch.id: ch for ch in topology.channels}

        # --- 1. Exactly one QM per app (Req 3.1) ---
        for app in topology.applications:
            if not app.connected_qm_id:
                violations.append(PolicyViolation(
                    policy_id="exactly_one_qm_per_app",
                    policy_name="Exactly One QM Per Application",
                    description=f"Application '{app.name}' ({app.id}) is not connected to any queue manager.",
                    offending_objects=[app.id],
                    remediation_suggestion="Assign the application to exactly one queue manager.",
                ))
            elif app.connected_qm_id not in qm_ids:
                violations.append(PolicyViolation(
                    policy_id="exactly_one_qm_per_app",
                    policy_name="Exactly One QM Per Application",
                    description=(
                        f"Application '{app.name}' ({app.id}) references queue manager "
                        f"'{app.connected_qm_id}' which does not exist in the topology."
                    ),
                    offending_objects=[app.id, app.connected_qm_id],
                    remediation_suggestion="Ensure the referenced queue manager exists in the topology.",
                ))

        # Check for apps connected to multiple QMs (via duplicate app IDs)
        app_qm_map: dict[str, set[str]] = {}
        for app in topology.applications:
            app_qm_map.setdefault(app.id, set()).add(app.connected_qm_id)
        for app_id, qms in app_qm_map.items():
            if len(qms) > 1:
                violations.append(PolicyViolation(
                    policy_id="exactly_one_qm_per_app",
                    policy_name="Exactly One QM Per Application",
                    description=(
                        f"Application '{app_id}' is connected to multiple queue managers: "
                        f"{sorted(qms)}."
                    ),
                    offending_objects=[app_id] + sorted(qms),
                    remediation_suggestion="Each application must connect to exactly one queue manager.",
                ))

        # --- 2. Producers write to remote queue on own QM (Req 3.2) ---
        for app in topology.applications:
            if app.role not in ("producer", "both"):
                continue
            for q_id in app.produces_to:
                queue = queue_by_id.get(q_id)
                if queue is None:
                    violations.append(PolicyViolation(
                        policy_id="producers_write_to_remote_queue_on_own_qm",
                        policy_name="Producers Write to Remote Queue on Own QM",
                        description=(
                            f"Producer '{app.name}' ({app.id}) references queue '{q_id}' "
                            f"which does not exist in the topology."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion="Ensure the target queue exists in the topology.",
                    ))
                    continue
                if queue.queue_type != QueueType.REMOTE:
                    violations.append(PolicyViolation(
                        policy_id="producers_write_to_remote_queue_on_own_qm",
                        policy_name="Producers Write to Remote Queue on Own QM",
                        description=(
                            f"Producer '{app.name}' ({app.id}) writes to queue '{queue.name}' "
                            f"({q_id}) which is of type '{queue.queue_type.value}', not 'remote'."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion="Producers must write to remote queues only.",
                    ))
                if queue.owning_qm_id != app.connected_qm_id:
                    violations.append(PolicyViolation(
                        policy_id="producers_write_to_remote_queue_on_own_qm",
                        policy_name="Producers Write to Remote Queue on Own QM",
                        description=(
                            f"Producer '{app.name}' ({app.id}) on QM '{app.connected_qm_id}' "
                            f"writes to queue '{queue.name}' ({q_id}) hosted on QM "
                            f"'{queue.owning_qm_id}'."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion=(
                            "Producers must write to remote queues on their own queue manager."
                        ),
                    ))

        # --- 3. Remote queue → xmitq → channel (Req 3.3) ---
        for queue in topology.queues:
            if queue.queue_type != QueueType.REMOTE:
                continue
            # Must have an xmitq
            if not queue.xmitq_id:
                violations.append(PolicyViolation(
                    policy_id="remote_queue_uses_xmitq_via_channel",
                    policy_name="Remote Queue Uses XMITQ via Channel",
                    description=(
                        f"Remote queue '{queue.name}' ({queue.id}) does not reference "
                        f"a transmission queue (xmitq)."
                    ),
                    offending_objects=[queue.id],
                    remediation_suggestion="Assign a transmission queue to the remote queue.",
                ))
            else:
                xmitq = queue_by_id.get(queue.xmitq_id)
                if xmitq is None:
                    violations.append(PolicyViolation(
                        policy_id="remote_queue_uses_xmitq_via_channel",
                        policy_name="Remote Queue Uses XMITQ via Channel",
                        description=(
                            f"Remote queue '{queue.name}' ({queue.id}) references xmitq "
                            f"'{queue.xmitq_id}' which does not exist."
                        ),
                        offending_objects=[queue.id, queue.xmitq_id],
                        remediation_suggestion="Ensure the referenced transmission queue exists.",
                    ))
                elif xmitq.queue_type != QueueType.TRANSMISSION:
                    violations.append(PolicyViolation(
                        policy_id="remote_queue_uses_xmitq_via_channel",
                        policy_name="Remote Queue Uses XMITQ via Channel",
                        description=(
                            f"Remote queue '{queue.name}' ({queue.id}) references queue "
                            f"'{xmitq.name}' ({xmitq.id}) as xmitq, but it is of type "
                            f"'{xmitq.queue_type.value}', not 'transmission'."
                        ),
                        offending_objects=[queue.id, xmitq.id],
                        remediation_suggestion="The xmitq reference must point to a transmission queue.",
                    ))

            # Must have a channel connecting the owning QM to the target QM
            if queue.target_qm_id and queue.owning_qm_id:
                has_channel = any(
                    ch.from_qm_id == queue.owning_qm_id and ch.to_qm_id == queue.target_qm_id
                    for ch in topology.channels
                )
                if not has_channel:
                    violations.append(PolicyViolation(
                        policy_id="remote_queue_uses_xmitq_via_channel",
                        policy_name="Remote Queue Uses XMITQ via Channel",
                        description=(
                            f"Remote queue '{queue.name}' ({queue.id}) routes from QM "
                            f"'{queue.owning_qm_id}' to QM '{queue.target_qm_id}' but no "
                            f"sender channel exists between them."
                        ),
                        offending_objects=[queue.id, queue.owning_qm_id, queue.target_qm_id],
                        remediation_suggestion=(
                            "Create a sender/receiver channel pair between the source and "
                            "destination queue managers."
                        ),
                    ))

        # --- 4. Deterministic channel naming (Req 3.4) ---
        for ch in topology.channels:
            expected_name = self._expected_channel_name(
                ch.from_qm_id, ch.to_qm_id, ch.direction,
            )
            if expected_name and ch.name != expected_name:
                violations.append(PolicyViolation(
                    policy_id="deterministic_channel_naming",
                    policy_name="Deterministic Channel Naming",
                    description=(
                        f"Channel '{ch.name}' ({ch.id}) does not follow deterministic naming. "
                        f"Expected '{expected_name}' for {ch.direction.value} channel from "
                        f"'{ch.from_qm_id}' to '{ch.to_qm_id}'."
                    ),
                    offending_objects=[ch.id],
                    remediation_suggestion=f"Rename channel to '{expected_name}'.",
                ))

        # --- 5. Consumers read from local queue on own QM (Req 3.5) ---
        for app in topology.applications:
            if app.role not in ("consumer", "both"):
                continue
            for q_id in app.consumes_from:
                queue = queue_by_id.get(q_id)
                if queue is None:
                    violations.append(PolicyViolation(
                        policy_id="consumers_read_from_local_queue_on_own_qm",
                        policy_name="Consumers Read from Local Queue on Own QM",
                        description=(
                            f"Consumer '{app.name}' ({app.id}) references queue '{q_id}' "
                            f"which does not exist in the topology."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion="Ensure the consumed queue exists in the topology.",
                    ))
                    continue
                if queue.queue_type != QueueType.LOCAL:
                    violations.append(PolicyViolation(
                        policy_id="consumers_read_from_local_queue_on_own_qm",
                        policy_name="Consumers Read from Local Queue on Own QM",
                        description=(
                            f"Consumer '{app.name}' ({app.id}) reads from queue '{queue.name}' "
                            f"({q_id}) which is of type '{queue.queue_type.value}', not 'local'."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion="Consumers must read from local queues only.",
                    ))
                if queue.owning_qm_id != app.connected_qm_id:
                    violations.append(PolicyViolation(
                        policy_id="consumers_read_from_local_queue_on_own_qm",
                        policy_name="Consumers Read from Local Queue on Own QM",
                        description=(
                            f"Consumer '{app.name}' ({app.id}) on QM '{app.connected_qm_id}' "
                            f"reads from queue '{queue.name}' ({q_id}) hosted on QM "
                            f"'{queue.owning_qm_id}'."
                        ),
                        offending_objects=[app.id, q_id],
                        remediation_suggestion=(
                            "Consumers must read from local queues on their own queue manager."
                        ),
                    ))

        return violations

    # ------------------------------------------------------------------
    # Sentinel policy evaluation  (Req 9.1)
    # ------------------------------------------------------------------

    def evaluate_sentinel(
        self, change_proposal: dict,
    ) -> list[PolicyViolation]:
        """Evaluate Sentinel policies against a proposed change.

        Sentinel-style policies enforce structural MQ constraints and
        security/neighbourhood alignment rules.

        Args:
            change_proposal: Dict with keys like ``topology``
                (TopologySnapshotEvent), ``affected_objects``, ``change_type``.

        Returns:
            List of violations (empty means all Sentinel policies pass).
        """
        violations: list[PolicyViolation] = []

        topology: TopologySnapshotEvent | None = change_proposal.get("topology")
        if topology is not None:
            violations.extend(self.validate_mq_constraints(topology))

        # --- secure_by_default ---
        violations.extend(self._check_secure_by_default(change_proposal))

        # --- neighbourhood_aligned ---
        violations.extend(self._check_neighbourhood_aligned(change_proposal))

        return violations

    # ------------------------------------------------------------------
    # OPA policy evaluation  (Req 9.2)
    # ------------------------------------------------------------------

    def evaluate_opa(
        self, change_proposal: dict, actor: dict,
    ) -> list[PolicyViolation]:
        """Evaluate OPA policies for access control and data validation.

        Args:
            change_proposal: Dict describing the proposed change.
            actor: Dict with ``role``, ``lob``, ``permissions`` keys.

        Returns:
            List of violations (empty means all OPA policies pass).
        """
        violations: list[PolicyViolation] = []

        # --- access_control_role_based ---
        violations.extend(self._check_access_control(change_proposal, actor))

        # --- data_validation_required_fields ---
        violations.extend(self._check_data_validation(change_proposal))

        # --- change_scope_limit ---
        violations.extend(self._check_change_scope(change_proposal))

        return violations

    # ------------------------------------------------------------------
    # Combined evaluation  (Req 9.1, 9.2, 9.3)
    # ------------------------------------------------------------------

    def evaluate_all(
        self, change_proposal: dict, actor: dict,
    ) -> tuple[bool, list[PolicyViolation]]:
        """Run all Sentinel + OPA policies.

        Returns:
            ``(passed, violations)`` where *passed* is True when there are
            zero violations.
        """
        violations: list[PolicyViolation] = []
        violations.extend(self.evaluate_sentinel(change_proposal))
        violations.extend(self.evaluate_opa(change_proposal, actor))
        return (len(violations) == 0, violations)

    # ------------------------------------------------------------------
    # LOB-specific policies  (Req 26.2)
    # ------------------------------------------------------------------

    def evaluate_lob_policies(
        self, change_proposal: dict, lob: str,
    ) -> list[PolicyViolation]:
        """Evaluate LOB-specific policies in addition to global policies.

        LOB policies enforce governance boundaries: objects must belong to
        the correct LOB, cross-LOB changes require additional approval, etc.
        """
        violations: list[PolicyViolation] = []

        topology: TopologySnapshotEvent | None = change_proposal.get("topology")
        if topology is None:
            return violations

        # Check that affected objects belong to the specified LOB
        for qm in topology.queue_managers:
            if qm.lob and qm.lob != lob:
                violations.append(PolicyViolation(
                    policy_id="lob_boundary_enforcement",
                    policy_name="LOB Boundary Enforcement",
                    description=(
                        f"Queue manager '{qm.name}' ({qm.id}) belongs to LOB '{qm.lob}' "
                        f"but the change targets LOB '{lob}'."
                    ),
                    offending_objects=[qm.id],
                    remediation_suggestion=(
                        "Ensure the change only affects objects within the target LOB, "
                        "or obtain cross-LOB approval."
                    ),
                ))

        for app in topology.applications:
            if app.lob and app.lob != lob:
                violations.append(PolicyViolation(
                    policy_id="lob_boundary_enforcement",
                    policy_name="LOB Boundary Enforcement",
                    description=(
                        f"Application '{app.name}' ({app.id}) belongs to LOB '{app.lob}' "
                        f"but the change targets LOB '{lob}'."
                    ),
                    offending_objects=[app.id],
                    remediation_suggestion=(
                        "Ensure the change only affects objects within the target LOB, "
                        "or obtain cross-LOB approval."
                    ),
                ))

        for queue in topology.queues:
            if queue.lob and queue.lob != lob:
                violations.append(PolicyViolation(
                    policy_id="lob_boundary_enforcement",
                    policy_name="LOB Boundary Enforcement",
                    description=(
                        f"Queue '{queue.name}' ({queue.id}) belongs to LOB '{queue.lob}' "
                        f"but the change targets LOB '{lob}'."
                    ),
                    offending_objects=[queue.id],
                    remediation_suggestion=(
                        "Ensure the change only affects objects within the target LOB, "
                        "or obtain cross-LOB approval."
                    ),
                ))

        for ch in topology.channels:
            if ch.lob and ch.lob != lob:
                violations.append(PolicyViolation(
                    policy_id="lob_boundary_enforcement",
                    policy_name="LOB Boundary Enforcement",
                    description=(
                        f"Channel '{ch.name}' ({ch.id}) belongs to LOB '{ch.lob}' "
                        f"but the change targets LOB '{lob}'."
                    ),
                    offending_objects=[ch.id],
                    remediation_suggestion=(
                        "Ensure the change only affects objects within the target LOB, "
                        "or obtain cross-LOB approval."
                    ),
                ))

        # Check cross-LOB channel dependencies
        qm_lob_map: dict[str, str | None] = {qm.id: qm.lob for qm in topology.queue_managers}
        for ch in topology.channels:
            from_lob = qm_lob_map.get(ch.from_qm_id)
            to_lob = qm_lob_map.get(ch.to_qm_id)
            if from_lob and to_lob and from_lob != to_lob:
                if from_lob == lob or to_lob == lob:
                    violations.append(PolicyViolation(
                        policy_id="lob_cross_boundary_channel",
                        policy_name="Cross-LOB Channel Dependency",
                        description=(
                            f"Channel '{ch.name}' ({ch.id}) crosses LOB boundary: "
                            f"from QM in LOB '{from_lob}' to QM in LOB '{to_lob}'."
                        ),
                        offending_objects=[ch.id, ch.from_qm_id, ch.to_qm_id],
                        remediation_suggestion=(
                            "Cross-LOB channels require approval from all affected LOB owners."
                        ),
                    ))

        return violations

    # ------------------------------------------------------------------
    # Compliance attestation  (Req 9.4)
    # ------------------------------------------------------------------

    def generate_compliance_attestation(
        self, change_proposal: dict,
    ) -> dict:
        """Generate a compliance attestation when all policies pass.

        The attestation is a structured dict suitable for attaching to a
        GitHub Pull Request or audit record.
        """
        actor = change_proposal.get("actor", {})
        passed, violations = self.evaluate_all(change_proposal, actor)

        if not passed:
            return {
                "attested": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "Policy violations detected",
                "violation_count": len(violations),
                "violations": [
                    {
                        "policy_id": v.policy_id,
                        "policy_name": v.policy_name,
                        "description": v.description,
                    }
                    for v in violations
                ],
            }

        attestation_id = str(uuid.uuid4())
        return {
            "attested": True,
            "attestation_id": attestation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "policies_evaluated": self.MQ_POLICIES + self.OPA_POLICIES,
            "policy_count": len(self.MQ_POLICIES) + len(self.OPA_POLICIES),
            "result": "PASS",
            "change_description": change_proposal.get("change_description", ""),
            "actor": actor.get("name", actor.get("role", "unknown")),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _expected_channel_name(
        from_qm_id: str, to_qm_id: str, direction: ChannelDirection,
    ) -> str:
        """Compute the expected deterministic channel name.

        For a message flow from QM_A → QM_B:
          Sender (on QM_A):   ``from_qm_id=QM_A, to_qm_id=QM_B`` → name ``QM_A.QM_B``
          Receiver (on QM_B): ``from_qm_id=QM_B, to_qm_id=QM_A`` → name ``QM_B.QM_A``

        Both directions use ``from_qm_id.to_qm_id`` because the Channel
        model stores the local QM as ``from_qm_id`` and the remote QM as
        ``to_qm_id``, matching the Req 3.4 naming convention.
        """
        if not from_qm_id or not to_qm_id:
            return ""
        return f"{from_qm_id}.{to_qm_id}"

    @staticmethod
    def _check_secure_by_default(
        change_proposal: dict,
    ) -> list[PolicyViolation]:
        """Check secure_by_default Sentinel policy.

        Ensures that channels have TLS enabled and queues have appropriate
        security settings.
        """
        violations: list[PolicyViolation] = []
        topology: TopologySnapshotEvent | None = change_proposal.get("topology")
        if topology is None:
            return violations

        for ch in topology.channels:
            meta = ch.metadata or {}
            if meta.get("tls_enabled") is False:
                violations.append(PolicyViolation(
                    policy_id="secure_by_default",
                    policy_name="Secure by Default",
                    description=(
                        f"Channel '{ch.name}' ({ch.id}) has TLS disabled."
                    ),
                    offending_objects=[ch.id],
                    remediation_suggestion="Enable TLS on all channels.",
                ))

        for q in topology.queues:
            meta = q.metadata or {}
            if meta.get("inhibit_put") is True and meta.get("inhibit_get") is True:
                violations.append(PolicyViolation(
                    policy_id="secure_by_default",
                    policy_name="Secure by Default",
                    description=(
                        f"Queue '{q.name}' ({q.id}) has both put and get inhibited, "
                        f"making it unusable."
                    ),
                    offending_objects=[q.id],
                    remediation_suggestion="Review inhibit settings — at least one operation should be allowed.",
                ))

        return violations

    @staticmethod
    def _check_neighbourhood_aligned(
        change_proposal: dict,
    ) -> list[PolicyViolation]:
        """Check neighbourhood_aligned Sentinel policy.

        Ensures channels connect queue managers in compatible neighbourhoods
        when neighbourhood metadata is available.
        """
        violations: list[PolicyViolation] = []
        topology: TopologySnapshotEvent | None = change_proposal.get("topology")
        if topology is None:
            return violations

        qm_neighbourhood: dict[str, str | None] = {
            qm.id: qm.neighborhood for qm in topology.queue_managers
        }

        for ch in topology.channels:
            from_nb = qm_neighbourhood.get(ch.from_qm_id)
            to_nb = qm_neighbourhood.get(ch.to_qm_id)
            # Only flag if both have neighbourhood metadata and they differ
            if from_nb and to_nb and from_nb != to_nb:
                violations.append(PolicyViolation(
                    policy_id="neighbourhood_aligned",
                    policy_name="Neighbourhood Aligned",
                    description=(
                        f"Channel '{ch.name}' ({ch.id}) connects QMs in different "
                        f"neighbourhoods: '{from_nb}' and '{to_nb}'."
                    ),
                    offending_objects=[ch.id, ch.from_qm_id, ch.to_qm_id],
                    remediation_suggestion=(
                        "Consider routing through a gateway QM or aligning neighbourhoods."
                    ),
                ))

        return violations

    @staticmethod
    def _check_access_control(
        change_proposal: dict, actor: dict,
    ) -> list[PolicyViolation]:
        """OPA: role-based access control check.

        Validates that the actor has the required role/permissions for the
        proposed change type.
        """
        violations: list[PolicyViolation] = []

        role = actor.get("role", "")
        permissions = actor.get("permissions", [])
        change_type = change_proposal.get("change_type", "")

        # Define required permissions per change type
        required_perms: dict[str, str] = {
            "create": "write",
            "update": "write",
            "delete": "admin",
            "topology_transform": "admin",
        }

        required = required_perms.get(change_type)
        if required and required not in permissions and role != "admin":
            violations.append(PolicyViolation(
                policy_id="access_control_role_based",
                policy_name="Role-Based Access Control",
                description=(
                    f"Actor with role '{role}' lacks '{required}' permission "
                    f"required for change type '{change_type}'."
                ),
                offending_objects=[],
                remediation_suggestion=(
                    f"Ensure the actor has '{required}' permission or 'admin' role."
                ),
            ))

        return violations

    @staticmethod
    def _check_data_validation(
        change_proposal: dict,
    ) -> list[PolicyViolation]:
        """OPA: data validation — required fields check.

        Ensures the change proposal contains all required fields.
        """
        violations: list[PolicyViolation] = []

        required_fields = ["change_type", "change_description"]
        for f in required_fields:
            if not change_proposal.get(f):
                violations.append(PolicyViolation(
                    policy_id="data_validation_required_fields",
                    policy_name="Data Validation — Required Fields",
                    description=f"Change proposal is missing required field '{f}'.",
                    offending_objects=[],
                    remediation_suggestion=f"Provide the '{f}' field in the change proposal.",
                ))

        return violations

    @staticmethod
    def _check_change_scope(
        change_proposal: dict,
    ) -> list[PolicyViolation]:
        """OPA: change scope limit.

        Prevents excessively large changes that could destabilise the topology.
        """
        violations: list[PolicyViolation] = []

        affected = change_proposal.get("affected_objects", [])
        max_scope = change_proposal.get("max_scope", 50)

        if len(affected) > max_scope:
            violations.append(PolicyViolation(
                policy_id="change_scope_limit",
                policy_name="Change Scope Limit",
                description=(
                    f"Change affects {len(affected)} objects, exceeding the maximum "
                    f"allowed scope of {max_scope}."
                ),
                offending_objects=affected[:10],
                remediation_suggestion=(
                    "Break the change into smaller batches or request a scope exception."
                ),
            ))

        return violations
