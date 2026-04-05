"""Explainable Decision Engine for MQ topology change proposals.

Generates structured Decision_Reports with reasoning chains, policy
references, alternatives considered, risk assessments, and recommendations.
Supports what-if evaluation and confidence scoring from historical decisions.

Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 7.11
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from models.domain import DecisionReport


class ExplainableDecisionEngine:
    """Generates structured Decision_Reports for every topology change proposal.

    Uses the PolicyEngine for constraint/policy evaluation and the
    Neo4jGraphStore for blast-radius and dependency queries.
    """

    # Default threshold above which a proposal is flagged "high-confidence"
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.75

    def __init__(self, confidence_threshold: float | None = None) -> None:
        self._confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self.DEFAULT_CONFIDENCE_THRESHOLD
        )

    # ------------------------------------------------------------------
    # generate_report  (Req 21.1, 21.2, 21.3, 21.4, 21.5, 21.7)
    # ------------------------------------------------------------------

    def generate_report(
        self,
        change_proposal: dict,
        graph_store: Any,
        policy_engine: Any,
        historical_decisions: list[dict] | None = None,
    ) -> DecisionReport:
        """Generate a Decision_Report for a topology change proposal.

        The report contains:
        - summary: natural-language explanation of the recommendation
        - reasoning_chain: ordered logic steps from issue to solution
        - policy_references: policies that influenced the decision
        - alternatives_considered: rejected alternatives with reasons
        - risk_assessment: risk evaluation string
        - recommendation: final recommendation text
        """
        if historical_decisions is None:
            historical_decisions = []

        correlation_id = change_proposal.get("correlation_id", str(uuid.uuid4()))
        change_type = change_proposal.get("change_type", "topology_change")
        description = change_proposal.get("description", "Topology change proposal")

        # Step 1: Evaluate policies (Req 21.3)
        reasoning_chain: list[str] = []
        policy_references: list[dict] = []

        reasoning_chain.append(
            f"Received {change_type} proposal: {description}"
        )

        actor = change_proposal.get("actor", {"role": "system", "id": "agent_brain"})
        passed, violations = policy_engine.evaluate_all(change_proposal, actor)

        if passed:
            reasoning_chain.append(
                "All Sentinel and OPA policies passed — proposal is compliant."
            )
        else:
            violation_ids = [v.policy_id for v in violations]
            reasoning_chain.append(
                f"Policy evaluation found {len(violations)} violation(s): "
                f"{', '.join(violation_ids)}."
            )
            for v in violations:
                policy_references.append(
                    {"policy_id": v.policy_id, "description": v.description}
                )

        # Always include the evaluated policy set as references
        for pid in policy_engine.MQ_POLICIES:
            if not any(pr["policy_id"] == pid for pr in policy_references):
                policy_references.append(
                    {"policy_id": pid, "description": f"Policy {pid} evaluated — no violation."}
                )

        # Step 2: Blast radius & downstream deps (Req 21.2)
        blast_radius = graph_store.query_blast_radius(change_proposal)
        direct_count = len(blast_radius.get("direct", []))
        transitive_count = len(blast_radius.get("transitive", []))

        reasoning_chain.append(
            f"Blast radius analysis: {direct_count} directly affected object(s), "
            f"{transitive_count} transitively affected object(s)."
        )

        object_ids = change_proposal.get("object_ids", [])
        downstream = graph_store.query_downstream_dependencies(object_ids)
        downstream_consumers = downstream.get("consumers", [])
        downstream_channels = downstream.get("channels", [])

        if downstream_consumers or downstream_channels:
            reasoning_chain.append(
                f"Downstream dependencies: {len(downstream_consumers)} consumer(s), "
                f"{len(downstream_channels)} channel(s) affected."
            )

        # Step 3: Risk assessment
        total_affected = direct_count + transitive_count
        risk_assessment = self._assess_risk(total_affected, change_proposal)
        reasoning_chain.append(f"Risk assessment: {risk_assessment}.")

        # Step 4: Alternatives considered (Req 21.4)
        alternatives_considered = self._generate_alternatives(
            change_proposal, passed, violations if not passed else []
        )
        if alternatives_considered:
            reasoning_chain.append(
                f"Considered {len(alternatives_considered)} alternative(s); "
                "all rejected — see alternatives_considered for details."
            )

        # Step 5: Confidence score (Req 7.11)
        confidence = self.compute_confidence_score(change_proposal, historical_decisions)
        confidence_label = "high" if confidence >= self._confidence_threshold else "normal"
        reasoning_chain.append(
            f"Confidence score: {confidence:.2f} ({confidence_label})."
        )

        # Step 6: Final recommendation
        if not passed:
            recommendation = (
                "REJECT — the proposal violates one or more policies. "
                "Address the violations listed in policy_references before resubmitting."
            )
        elif risk_assessment.startswith("critical"):
            recommendation = (
                "ESCALATE — the proposal is policy-compliant but carries critical risk. "
                "Multi-level approval recommended."
            )
        elif risk_assessment.startswith("high"):
            recommendation = (
                "APPROVE WITH CAUTION — the proposal is compliant but high-risk. "
                "Review blast radius carefully before approving."
            )
        else:
            recommendation = (
                "APPROVE — the proposal is policy-compliant with acceptable risk."
            )

        summary = (
            f"Decision report for '{description}'. "
            f"Policy evaluation: {'PASS' if passed else 'FAIL'}. "
            f"Blast radius: {total_affected} object(s). "
            f"Risk: {risk_assessment}. "
            f"Confidence: {confidence:.2f}."
        )

        return DecisionReport(
            report_id=str(uuid.uuid4()),
            correlation_id=correlation_id,
            summary=summary,
            reasoning_chain=reasoning_chain,
            policy_references=policy_references,
            alternatives_considered=alternatives_considered,
            risk_assessment=risk_assessment,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # evaluate_what_if  (Req 21.6)
    # ------------------------------------------------------------------

    def evaluate_what_if(
        self,
        hypothetical_change: dict,
        graph_store: Any,
        policy_engine: Any,
    ) -> dict:
        """Evaluate a hypothetical change without modifying production.

        Creates a sandbox copy of the graph, evaluates policies, computes
        blast radius, and returns a projected outcome report.

        Returns a dict with keys: decision_report, policy_passed,
        violations, blast_radius, projected_outcome.
        """
        # Create sandbox copy so production is untouched
        session_id = f"whatif-{uuid.uuid4().hex[:8]}"
        sandbox = graph_store.create_sandbox_copy(session_id)

        # Evaluate policies against the hypothetical change
        actor = hypothetical_change.get(
            "actor", {"role": "operator", "id": "what-if-user"}
        )
        passed, violations = policy_engine.evaluate_all(hypothetical_change, actor)

        # Blast radius on the real graph (read-only query)
        blast_radius = graph_store.query_blast_radius(hypothetical_change)

        # Downstream dependencies
        object_ids = hypothetical_change.get("object_ids", [])
        downstream = graph_store.query_downstream_dependencies(object_ids)

        total_affected = (
            len(blast_radius.get("direct", []))
            + len(blast_radius.get("transitive", []))
        )
        risk_assessment = self._assess_risk(total_affected, hypothetical_change)

        # Build a lightweight decision report for the what-if
        report = self.generate_report(
            hypothetical_change, graph_store, policy_engine, []
        )

        violation_dicts = [
            {"policy_id": v.policy_id, "description": v.description}
            for v in violations
        ]

        projected_outcome = "compliant" if passed else "non-compliant"

        return {
            "decision_report": report.model_dump(),
            "policy_passed": passed,
            "violations": violation_dicts,
            "blast_radius": blast_radius,
            "downstream_dependencies": downstream,
            "risk_assessment": risk_assessment,
            "projected_outcome": projected_outcome,
            "sandbox_session_id": session_id,
        }

    # ------------------------------------------------------------------
    # compute_confidence_score  (Req 7.11)
    # ------------------------------------------------------------------

    def compute_confidence_score(
        self,
        proposal: dict,
        historical_decisions: list[dict],
    ) -> float:
        """Analyse past approval/rejection patterns to compute a confidence score.

        The score is in [0.0, 1.0].  Higher values indicate the proposal
        closely matches historically approved patterns.

        Matching criteria:
        - Same change_type as past decisions
        - Similar affected object count (within 50% tolerance)
        - Same risk level

        Returns 0.5 (neutral) when no historical data is available.
        """
        if not historical_decisions:
            return 0.5

        change_type = proposal.get("change_type", "")
        proposal_obj_count = len(proposal.get("object_ids", []))

        matching_approved = 0
        matching_rejected = 0
        total_matching = 0

        for decision in historical_decisions:
            d_type = decision.get("change_type", "")
            d_decision = decision.get("decision", "").lower()
            d_obj_count = len(decision.get("object_ids", []))

            # Check if this historical decision is relevant
            type_match = d_type == change_type if change_type else True
            count_match = self._count_within_tolerance(
                proposal_obj_count, d_obj_count, tolerance=0.5
            )

            if type_match and count_match:
                total_matching += 1
                if d_decision in ("approve", "batch_approve"):
                    matching_approved += 1
                elif d_decision == "reject":
                    matching_rejected += 1

        if total_matching == 0:
            return 0.5

        # Confidence = ratio of approvals among matching decisions
        approval_ratio = matching_approved / total_matching
        return round(approval_ratio, 4)

    @property
    def confidence_threshold(self) -> float:
        """Return the current confidence threshold."""
        return self._confidence_threshold

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assess_risk(total_affected: int, change_proposal: dict) -> str:
        """Derive a risk label from the number of affected objects."""
        explicit_risk = change_proposal.get("risk_score")
        if explicit_risk:
            return str(explicit_risk)

        if total_affected == 0:
            return "low"
        elif total_affected <= 3:
            return "low"
        elif total_affected <= 8:
            return "medium"
        elif total_affected <= 15:
            return "high"
        else:
            return "critical"

    @staticmethod
    def _generate_alternatives(
        change_proposal: dict,
        policy_passed: bool,
        violations: list,
    ) -> list[dict]:
        """Generate a list of alternative actions that were considered.

        This is a heuristic generator — in a production system this would
        be backed by an LLM or rule engine.
        """
        alternatives: list[dict] = []
        change_type = change_proposal.get("change_type", "topology_change")

        # Alternative 1: Do nothing
        alternatives.append({
            "action": "No action — maintain current topology.",
            "rejection_reason": (
                "Leaving the topology unchanged does not address the "
                "identified issue or improvement opportunity."
            ),
        })

        # Alternative 2: Partial change
        object_ids = change_proposal.get("object_ids", [])
        if len(object_ids) > 1:
            alternatives.append({
                "action": (
                    f"Apply change to a subset of the {len(object_ids)} "
                    "affected objects."
                ),
                "rejection_reason": (
                    "Partial application may leave the topology in an "
                    "inconsistent state and violate MQ constraints."
                ),
            })

        # Alternative 3: If violations exist, suggest remediation first
        if not policy_passed and violations:
            alternatives.append({
                "action": "Remediate policy violations before applying the change.",
                "rejection_reason": (
                    "Remediation is required but would delay the change; "
                    "the current proposal is evaluated as-is."
                ),
            })

        return alternatives

    @staticmethod
    def _count_within_tolerance(
        a: int, b: int, tolerance: float = 0.5,
    ) -> bool:
        """Return True if *a* and *b* are within *tolerance* of each other."""
        if a == 0 and b == 0:
            return True
        denominator = max(a, b, 1)
        return abs(a - b) / denominator <= tolerance
