"""Agent Brain — LangGraph workflow orchestration.

Implements Bootstrap and Steady-State workflow graphs using LangGraph.
Each node function operates on AgentState and delegates to the
appropriate engine (transformer, policy, decision, impact, etc.).

The LLM is used for:
  - Generating natural-language explanations of decisions
  - Anomaly analysis and remediation suggestions
  - Chatbot responses

Requirements: 14.1, 14.2, 14.3, 14.4, 14.6, 14.7
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_brain.llm_config import get_llm, FallbackLLM
from agent_brain.state import AgentState
from graph_store.neo4j_client import Neo4jGraphStore
from transformer.analyzer import analyze_topology, edges_to_dot
from transformer.transform import run_transform
from transformer.validators import validate_edges_target
from policy_engine.engine import PolicyEngine
from decision_engine.engine import ExplainableDecisionEngine
from impact_analysis.engine import ImpactAnalysisEngine

logger = logging.getLogger(__name__)


class AgentBrainGraph:
    """LangGraph workflow definition for the Agent_Brain.

    Provides both a LangGraph-based execution path (when langgraph is
    available) and a simple sequential fallback for environments where
    LangGraph isn't installed.
    """

    def __init__(
        self,
        graph_store: Neo4jGraphStore | None = None,
        llm: Any | None = None,
    ) -> None:
        self.graph_store = graph_store or Neo4jGraphStore()
        self.policy_engine = PolicyEngine()
        self.decision_engine = ExplainableDecisionEngine()
        self.impact_engine = ImpactAnalysisEngine()
        self.llm = llm or get_llm()

    # ------------------------------------------------------------------
    # Bootstrap workflow — sequential execution
    # ------------------------------------------------------------------

    def run_bootstrap(self, csv_path: str, out_dir: str = "artifacts") -> AgentState:
        """Execute the full Bootstrap workflow sequentially.

        Steps: ingest_csv → transform → load_to_graph → detect_anomalies →
               compute_impact → generate_decision_report → explain_with_llm
        """
        state = AgentState(
            current_mode="bootstrap",
            csv_path=csv_path,
            correlation_id=str(uuid.uuid4()),
        )

        try:
            state = self.ingest_csv(state)
            state = self.transform_topology(state, out_dir)
            state = self.load_target_to_graph(state)
            state = self.detect_anomalies(state)
            state = self.compute_impact_analysis(state)
            state = self.generate_decision_report(state)
            state = self.explain_with_llm(state)
            state = self.persist_audit(state, "bootstrap_complete")
        except Exception as e:
            state.error = str(e)
            logger.error("Bootstrap failed: %s", e, exc_info=True)

        return state

    # ------------------------------------------------------------------
    # Node functions
    # ------------------------------------------------------------------

    def ingest_csv(self, state: AgentState) -> AgentState:
        """Parse and analyze the AS-IS CSV."""
        if not state.csv_path:
            state.error = "No CSV path provided"
            return state

        metrics, extras = analyze_topology(state.csv_path)
        state.as_is_metrics = metrics
        state.snapshot = {
            "edges": extras.get("edges", []),
            "qm_in": extras.get("qm_in", {}),
            "qm_out": extras.get("qm_out", {}),
        }

        state.audit_log.append({
            "action": "ingest_csv",
            "correlation_id": state.correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {"rows": metrics.get("rows", 0), "path": state.csv_path},
        })

        logger.info("Ingested CSV: %d edges, %d QMs",
                     metrics.get("total_edges", 0),
                     metrics.get("unique_queue_managers", 0))
        return state

    def transform_topology(self, state: AgentState, out_dir: str = "artifacts") -> AgentState:
        """Transform AS-IS → TARGET topology."""
        if not state.csv_path:
            state.error = "No CSV path for transformation"
            return state

        os.makedirs(out_dir, exist_ok=True)
        result = run_transform(state.csv_path, out_dir)

        if result.get("error"):
            state.error = result["error"]
            return state

        state.target_metrics = result.get("target_report", {}).get("topology_metrics", {})
        state.target_dot = result.get("target_dot", "")
        state.iac_artifacts = result.get("artifacts", {})

        state.audit_log.append({
            "action": "transform_topology",
            "correlation_id": state.correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "target_nodes": state.target_metrics.get("total_nodes", 0),
                "target_edges": state.target_metrics.get("total_edges", 0),
            },
        })

        logger.info("Transformation complete: %d target edges",
                     state.target_metrics.get("total_edges", 0))
        return state

    def load_target_to_graph(self, state: AgentState) -> AgentState:
        """Load target topology edges into the graph store."""
        edges_path = (state.iac_artifacts or {}).get("edges_target")
        if edges_path and os.path.exists(edges_path):
            count = self.graph_store.load_edges_csv(edges_path)
            state.graph_data = {"edges_loaded": count}
            logger.info("Loaded %d edges into graph store", count)
        return state

    def detect_anomalies(self, state: AgentState) -> AgentState:
        """Detect anomalies in the topology using graph constraint checks."""
        check_result = self.graph_store.check_constraints()
        if not check_result["ok"]:
            for err in check_result["errors"]:
                state.anomalies.append({
                    "type": "constraint_violation",
                    "severity": "warning",
                    "description": err,
                    "correlation_id": state.correlation_id,
                })
        logger.info("Anomaly detection: %d issues found", len(state.anomalies))
        return state

    def compute_impact_analysis(self, state: AgentState) -> AgentState:
        """Compute blast radius and risk for the transformation."""
        hubs = self.graph_store.top_hubs(k=5)
        hub_ids = [h[0] for h in hubs]

        if hub_ids:
            blast = self.impact_engine.compute_blast_radius(
                {"object_ids": hub_ids}, self.graph_store
            )
            risk = self.impact_engine.compute_risk_score(blast)
            state.impact_analysis = {
                "blast_radius": blast,
                "risk_score": risk,
                "top_hubs": hubs,
            }
        return state

    def generate_decision_report(self, state: AgentState) -> AgentState:
        """Generate an explainable decision report."""
        proposal = {
            "description": "Bootstrap topology transformation",
            "change_type": "topology_transform",
            "change_description": "Transform AS-IS MQ topology to compliant target state",
            "object_ids": [],
            "correlation_id": state.correlation_id,
            "actor": {"role": "admin", "id": "agent_brain", "permissions": ["write", "admin"]},
        }

        report = self.decision_engine.generate_report(
            proposal, self.graph_store, self.policy_engine
        )
        state.decision_report = report.model_dump()
        return state

    def explain_with_llm(self, state: AgentState) -> AgentState:
        """Use the LLM to generate a natural-language explanation."""
        as_is = state.as_is_metrics or {}
        target = state.target_metrics or {}
        anomalies = state.anomalies
        impact = state.impact_analysis or {}

        prompt = f"""You are the MQ Guardian intelligent agent. Analyze this MQ topology transformation and provide a clear, concise explanation.

AS-IS Topology:
- Total nodes: {as_is.get('total_nodes', 'N/A')}
- Total edges: {as_is.get('total_edges', 'N/A')}
- Unique queue managers: {as_is.get('unique_queue_managers', 'N/A')}
- Brittle apps (connected to >1 QM): {as_is.get('brittle_apps', 'N/A')}
- Topology Complexity Score (TCS): {as_is.get('tcs_score', 'N/A')}

TARGET Topology (after transformation):
- Total nodes: {target.get('total_nodes', 'N/A')}
- Total edges: {target.get('total_edges', 'N/A')}
- Unique queue managers: {target.get('unique_queue_managers', 'N/A')}
- Brittle apps: {target.get('brittle_apps', 0)} (enforced 1-QM-per-App)
- TCS: {target.get('tcs_score', 'N/A')}

Anomalies detected: {len(anomalies)}
Risk assessment: {impact.get('risk_score', 'N/A')}

Constraints enforced:
1. Each application connects to exactly one Queue Manager
2. Producers write to Remote Queues on their own QM
3. Remote Queues route via Transmission Queues and Channels
4. Channel naming follows deterministic pattern (fromQM.toQM)
5. Consumers read from Local Queues on their own QM

Provide:
1. A summary of what changed and why
2. Key improvements achieved
3. Any risks or concerns
4. Recommendation (approve/reject/modify)

Keep it concise — 3-4 paragraphs max."""

        try:
            response = self.llm.invoke(prompt)
            state.explanation = getattr(response, "content", str(response))
            logger.info("LLM explanation generated (%d chars)", len(state.explanation))
        except Exception as e:
            state.explanation = (
                f"[LLM unavailable: {e}]\n\n"
                f"Topology transformed from {as_is.get('total_edges', '?')} edges to "
                f"{target.get('total_edges', '?')} edges. "
                f"Brittle apps reduced from {as_is.get('brittle_apps', '?')} to "
                f"{target.get('brittle_apps', 0)}. "
                f"All MQ constraints enforced by construction."
            )
            logger.warning("LLM call failed, using template: %s", e)

        return state

    def persist_audit(self, state: AgentState, action: str) -> AgentState:
        """Persist an audit event to the graph store."""
        self.graph_store.persist_audit_event({
            "event_id": str(uuid.uuid4()),
            "correlation_id": state.correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": "agent_brain",
            "action_type": action,
            "affected_objects": [],
            "outcome": "error" if state.error else "success",
            "details": {
                "mode": state.current_mode,
                "anomaly_count": len(state.anomalies),
                "error": state.error,
            },
        })
        return state

    # ------------------------------------------------------------------
    # Chatbot query handler — uses LLM for natural language responses
    # ------------------------------------------------------------------

    def handle_query(self, query: str, state: AgentState | None = None) -> str:
        """Handle a natural-language query about the topology.

        Used by the chatbot interface. The LLM receives context about
        the current topology state and generates a response.
        """
        context_parts = []
        if state and state.as_is_metrics:
            context_parts.append(f"AS-IS: {state.as_is_metrics}")
        if state and state.target_metrics:
            context_parts.append(f"TARGET: {state.target_metrics}")
        if state and state.anomalies:
            context_parts.append(f"Anomalies: {len(state.anomalies)}")

        context = "\n".join(context_parts) if context_parts else "No topology loaded yet."

        prompt = f"""You are the MQ Guardian intelligent agent chatbot. Answer the user's question about the MQ topology.

Current topology context:
{context}

User question: {query}

Provide a helpful, concise answer. If you don't have enough information, say so."""

        try:
            response = self.llm.invoke(prompt)
            return getattr(response, "content", str(response))
        except Exception as e:
            return f"I'm having trouble connecting to the LLM ({e}). The topology data is available through the API endpoints."

    # ------------------------------------------------------------------
    # LangGraph StateGraph builder (when langgraph is available)
    # ------------------------------------------------------------------

    def build_bootstrap_graph(self) -> Any:
        """Build a LangGraph StateGraph for Bootstrap mode.

        Falls back to sequential execution if langgraph is not installed.
        """
        try:
            from langgraph.graph import StateGraph, END

            graph = StateGraph(AgentState)

            graph.add_node("ingest_csv", self.ingest_csv)
            graph.add_node("transform_topology", lambda s: self.transform_topology(s))
            graph.add_node("load_target_to_graph", self.load_target_to_graph)
            graph.add_node("detect_anomalies", self.detect_anomalies)
            graph.add_node("compute_impact", self.compute_impact_analysis)
            graph.add_node("generate_report", self.generate_decision_report)
            graph.add_node("explain", self.explain_with_llm)

            graph.set_entry_point("ingest_csv")
            graph.add_edge("ingest_csv", "transform_topology")
            graph.add_edge("transform_topology", "load_target_to_graph")
            graph.add_edge("load_target_to_graph", "detect_anomalies")
            graph.add_edge("detect_anomalies", "compute_impact")
            graph.add_edge("compute_impact", "generate_report")
            graph.add_edge("generate_report", "explain")
            graph.add_edge("explain", END)

            return graph.compile()

        except ImportError:
            logger.info("LangGraph not available — using sequential execution")
            return None
