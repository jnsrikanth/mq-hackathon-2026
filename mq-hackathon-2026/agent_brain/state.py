"""Agent Brain state model for LangGraph workflows.

Defines the Pydantic state that flows through the LangGraph StateGraph
nodes during both Bootstrap and Steady-State modes.

Requirements: 14.5
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Pydantic state model for the Agent_Brain LangGraph workflow."""

    current_mode: Literal["bootstrap", "steady_state"] = "bootstrap"

    # Topology data
    csv_path: Optional[str] = None
    snapshot: Optional[dict] = None
    graph_data: Optional[dict] = None
    as_is_metrics: Optional[dict] = None
    target_metrics: Optional[dict] = None
    target_dot: Optional[str] = None

    # Anomalies and drift
    anomalies: list[dict] = Field(default_factory=list)
    drift_events: list[dict] = Field(default_factory=list)

    # Change management
    proposed_fixes: list[dict] = Field(default_factory=list)
    human_approval_needed: bool = False
    approval_id: Optional[str] = None
    approval_decision: Optional[str] = None

    # Traceability
    correlation_id: Optional[str] = None

    # Intelligence outputs
    impact_analysis: Optional[dict] = None
    decision_report: Optional[dict] = None
    confidence_scores: dict[str, float] = Field(default_factory=dict)

    # IaC artifacts
    iac_artifacts: Optional[dict] = None

    # Audit trail
    audit_log: list[dict] = Field(default_factory=list)

    # LLM explanation
    explanation: Optional[str] = None

    # Error tracking
    error: Optional[str] = None

    # Timestamps
    timestamp: datetime = Field(default_factory=datetime.utcnow)
