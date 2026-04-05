# Implementation Plan: MQ Guardian Platform

## Overview

Implement the MQ Guardian Platform — an enterprise-grade IBM MQ topology management system with dual-mode operation (Bootstrap and Steady-State). The implementation follows a bottom-up approach: foundation and data models first, then core infrastructure (Neo4j, Kafka), core logic engines, Agent Brain orchestration, IaC pipelines, intelligence layer, human interfaces, observability, security, and finally integration testing with Docker Compose validation.

All code is Python. Key frameworks: LangGraph/LangChain, Confluent Kafka, Neo4j driver, FastAPI, Chainlit, Terraform/Ansible generators, Prometheus client, Prophet ML.

## Tasks

- [x] 1. Project structure, configuration, and core data models
  - [x] 1.1 Create project directory structure and Python package scaffolding
    - Create top-level directories: `transformer/`, `graph_store/`, `agent_brain/`, `event_bus/`, `iac_engine/`, `policy_engine/`, `decision_engine/`, `impact_analysis/`, `capacity_planning/`, `optimizer/`, `sandbox/`, `chatbot/`, `monitoring/`, `models/`, `api/`, `tests/`
    - Create `pyproject.toml` with all dependencies (neo4j, confluent-kafka, langgraph, langchain, fastapi, chainlit, prophet, pydantic, prometheus-client, jinja2, python-hcl2)
    - Create `__init__.py` files for all packages
    - _Requirements: 18.1_

  - [x] 1.2 Implement core domain models (`models/domain.py`)
    - Implement all Pydantic models: `QueueManager`, `Queue`, `Channel`, `Application`, `TopologySnapshotEvent`, `AnomalyEvent`, `DriftEvent`, `ApprovalRequest`, `ApprovalResponse`, `DecisionReport`, `AuditEvent`, `ComplexityMetricModel`, `CapacityAlert`, `SandboxSession`
    - Implement all enums: `QueueType`, `ChannelDirection`, `AnomalySeverity`, `RiskScore`, `DriftType`, `ApprovalDecision`, `OperationalMode`
    - _Requirements: 1.5, 2.2, 5.1, 6.2, 6.6, 7.1, 7.12, 12.2, 14.5, 21.7, 22.6_

  - [x] 1.3 Write unit tests for domain models
    - Test Pydantic validation, serialization/deserialization, enum values, optional fields
    - _Requirements: 1.5, 14.5_

  - [x] 1.4 Implement transformer data models (`transformer/core.py` — dataclasses and enums only)
    - Implement `CSVValidationError`, `ObjectType` enum, `MQObject`, `TopologySnapshot`, `ComplexityMetric`, `ComplexityComparison` dataclasses
    - _Requirements: 1.1, 1.5, 5.1_

- [x] 2. Kafka Event Bus schemas and infrastructure
  - [x] 2.1 Implement Kafka event schemas (`event_bus/schemas.py`)
    - Define `KAFKA_TOPICS` dict with all 9 topics and descriptions
    - Define `BASE_EVENT_SCHEMA`, `APPROVAL_REQUEST_SCHEMA`, `HUMAN_FEEDBACK_SCHEMA`, `DECISION_REPORT_SCHEMA` JSON schemas
    - Implement schema validation utility function `validate_message(topic, message) -> (bool, errors)`
    - _Requirements: 13.1, 13.2, 13.3, 13.5_

  - [x] 2.2 Implement Kafka producer wrapper (`event_bus/producer.py`)
    - Create `EventBusProducer` class wrapping `confluent_kafka.Producer`
    - Implement `publish(topic, event)` with schema validation before produce
    - Implement `publish_with_correlation(topic, event, correlation_id)` to auto-attach correlation_id
    - Implement dead-letter routing on schema validation failure
    - _Requirements: 13.2, 13.3, 13.5_

  - [x] 2.3 Implement Kafka consumer wrapper (`event_bus/consumer.py`)
    - Create `EventBusConsumer` class wrapping `confluent_kafka.Consumer`
    - Implement configurable consumer group, multi-topic subscription
    - Implement message deserialization with schema validation
    - _Requirements: 13.1, 13.4, 14.9, 14.10_

  - [x] 2.4 Write unit tests for event schemas and producer/consumer
    - Test schema validation accepts valid messages, rejects invalid
    - Test correlation_id attachment
    - _Requirements: 13.2, 13.3, 13.5_

- [x] 3. Neo4j Graph Store
  - [x] 3.1 Implement Neo4j schema initialization (`graph_store/schema.py`)
    - Create function to run Cypher constraints: `qm_unique`, `queue_unique`, `app_unique`, `channel_unique`, `audit_unique`
    - Create indexes for common query patterns (LOB, version tag, correlation_id)
    - _Requirements: 2.1, 2.4_

  - [x] 3.2 Implement Neo4j client (`graph_store/neo4j_client.py`)
    - Implement `Neo4jGraphStore.__init__` with connection setup
    - Implement `load_snapshot` — create/update nodes and edges for all MQ objects, preserve CSV attributes, retain previous snapshot as versioned record
    - Implement `get_current_topology` with optional LOB filter
    - Implement `get_historical_snapshot` by version tag
    - Implement `query_blast_radius` — graph traversal for direct and transitive affected objects
    - Implement `query_downstream_dependencies` — find downstream consumers, channels, xmitqs
    - Implement `query_cross_lob_dependencies` — identify cross-LOB channels and flows
    - Implement `assign_lob` — assign MQ object to a LOB
    - Implement `persist_audit_event` — store immutable audit record
    - Implement `query_audit_log` — query with filters (time_range, action_type, actor, affected_object, correlation_id)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 12.2, 12.5, 26.1, 26.6, 26.7_

  - [x] 3.3 Implement sandbox graph operations (`graph_store/neo4j_client.py`)
    - Implement `create_sandbox_copy` — in-memory copy of target topology for simulation
    - _Requirements: 25.1, 25.2_

  - [x] 3.4 Write unit tests for Neo4j client
    - Test snapshot loading, versioning, blast radius queries, audit persistence, LOB filtering
    - Mock Neo4j driver for unit tests
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 12.2_

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. CSV Transformer and Complexity Analyzer
  - [x] 5.1 Implement CSV parser (`transformer/core.py`)
    - Implement `CSVParser.parse_file` — parse CSV with column validation, raise `CSVValidationError` on malformed rows, complete within 30s for 14K rows
    - Implement `CSVParser.validate_row` — validate required fields and data types per row
    - _Requirements: 1.1, 1.3, 1.5_

  - [x] 5.2 Implement topology transformer (`transformer/core.py`)
    - Implement `TopologyTransformer.transform` — produce target topology with reduced channels, eliminated redundant objects, minimized hops, no cycles, balanced fan-in/out
    - Implement `_eliminate_redundant_objects` — remove unused QMs, orphaned queues, redundant channels
    - Implement `_minimize_routing_hops` — optimize channel routing
    - Implement `_detect_cycles` — DFS-based cycle detection in channel routing graph
    - Implement `_balance_fan_in_out` — redistribute channels to avoid excessive fan-in/fan-out
    - Implement `export_to_csv` — export topology to CSV with structure identical to input format
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 15.1, 15.2_

  - [x] 5.3 Implement complexity analyzer (`transformer/core.py`)
    - Implement `ComplexityAnalyzer.compute_metric` — compute composite score from channel count, avg hops, fan-in/out ratios, redundant objects
    - Implement `ComplexityAnalyzer.compare` — produce comparative analysis with absolute and percentage changes
    - _Requirements: 5.1, 5.2, 5.4_

  - [x] 5.4 Write unit tests for CSV parser, transformer, and complexity analyzer
    - Test CSV parsing with valid/invalid data, malformed rows, missing columns
    - Test transformation reduces channels, eliminates redundant objects, detects cycles
    - Test complexity metric computation and comparison
    - _Requirements: 1.1, 1.3, 4.1, 4.2, 4.3, 4.5, 5.1, 5.2_

- [x] 6. Policy Engine (Sentinel + OPA)
  - [x] 6.1 Implement policy engine (`policy_engine/engine.py`)
    - Implement `PolicyViolation` dataclass
    - Implement `PolicyEngine` with `MQ_POLICIES` list
    - Implement `validate_mq_constraints` — validate full topology against all MQ constraints (1 QM per app, producer→remoteQ, remoteQ→xmitq→channel, deterministic naming, consumer→localQ)
    - Implement `evaluate_sentinel` — evaluate Sentinel policies against proposed changes
    - Implement `evaluate_opa` — evaluate OPA policies for access control and data validation
    - Implement `evaluate_all` — run all Sentinel + OPA policies, return (passed, violations)
    - Implement `evaluate_lob_policies` — evaluate LOB-specific policies in addition to global
    - Implement `generate_compliance_attestation` — generate attestation when all policies pass
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 9.1, 9.2, 9.3, 9.4, 26.2_

  - [x] 6.2 Write unit tests for policy engine
    - Test each MQ constraint individually with valid and violating topologies
    - Test compliance attestation generation
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 9.1, 9.2, 9.3_

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Explainable Decision Engine
  - [x] 8.1 Implement decision engine (`decision_engine/engine.py`)
    - Implement `ExplainableDecisionEngine.generate_report` — produce Decision_Report with summary, reasoning_chain, policy_references, alternatives_considered, risk_assessment, recommendation
    - Implement `evaluate_what_if` — evaluate hypothetical change without modifying production, return projected outcome
    - Implement `compute_confidence_score` — analyze past approval/rejection patterns, flag high-confidence proposals
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 7.11_

  - [x] 8.2 Write unit tests for decision engine
    - Test report generation structure, what-if evaluation, confidence scoring
    - _Requirements: 21.1, 21.7_

- [x] 9. Impact Analysis Engine
  - [x] 9.1 Implement impact analysis engine (`impact_analysis/engine.py`)
    - Implement `ImpactAnalysisEngine.compute_blast_radius` — traverse graph for direct and transitive affected objects
    - Implement `compute_risk_score` — estimate risk (low/medium/high/critical) based on affected count, app criticality, historical outcomes
    - Implement `identify_downstream_dependencies` — find downstream consumers, channels, xmitqs
    - Implement `generate_rollback_plan` — exact reversal actions to restore pre-change state
    - Implement `simulate_change` — simulate against graph model without production modification, validate post-change constraints
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7_

  - [x] 9.2 Write unit tests for impact analysis engine
    - Test blast radius computation, risk scoring, rollback plan generation
    - _Requirements: 22.1, 22.2, 22.4_

- [x] 10. Capacity Planning and Forecasting
  - [x] 10.1 Implement capacity planning engine (`capacity_planning/engine.py`)
    - Implement `CapacityPlanningEngine.analyze_growth_patterns` — analyze QM/queue/channel/message volume trends
    - Implement `forecast_capacity` — Prophet ML forecasting for capacity threshold breaches
    - Implement `simulate_onboarding_impact` — forecast impact of new app on topology complexity
    - Implement `generate_capacity_report` — weekly report with current util, projected at 30/60/90 days, recommended actions
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.5, 23.6_

  - [x] 10.2 Write unit tests for capacity planning engine
    - Test growth pattern analysis, forecast generation, report structure
    - _Requirements: 23.1, 23.2, 23.5_

- [x] 11. Topology Optimizer
  - [x] 11.1 Implement topology optimizer (`optimizer/engine.py`)
    - Implement `TopologyOptimizer.identify_consolidation_candidates` — find QMs consolidatable without constraint violations
    - Implement `identify_channel_elimination` — find channels eliminable by rerouting
    - Implement `suggest_clustering_optimizations` — neighborhood/region-based clustering suggestions
    - Implement `recommend_placement` — optimal QM placement for new apps
    - Implement `calculate_combined_improvement` — individual and combined complexity reduction
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5_

  - [x] 11.2 Write unit tests for topology optimizer
    - Test consolidation candidate identification, channel elimination, placement recommendation
    - _Requirements: 24.1, 24.2, 24.4, 24.5_

- [x] 12. Sandbox Engine
  - [x] 12.1 Implement sandbox engine (`sandbox/engine.py`)
    - Implement `SandboxEngine.create_session` — create isolated in-memory topology copy
    - Implement `apply_change` — apply change to sandbox, return before/after complexity metrics
    - Implement `compose_changes` — apply changes sequentially, show cumulative impact
    - Implement `validate_sandbox` — validate sandbox topology against Policy_Engine
    - Implement `export_as_proposal` — export simulated state as formal change proposal for HiTL
    - Implement `destroy_session` — discard sandbox without affecting production
    - Support concurrent sessions for different operators
    - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 25.7, 25.8_

  - [x] 12.2 Write unit tests for sandbox engine
    - Test session creation, change application, composition, export, isolation
    - _Requirements: 25.1, 25.2, 25.3, 25.6, 25.7_

- [x] 13. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Agent Brain — State model and Kafka integration
  - [x] 14.1 Implement Agent Brain state model (`agent_brain/state.py`)
    - Implement `AgentState` Pydantic model with all fields: current_mode, snapshot, graph_data, anomalies, drift_events, proposed_fixes, human_approval_needed, approval_id, correlation_id, impact_analysis, decision_report, audit_log, timestamp, confidence_scores
    - _Requirements: 14.5_

  - [x] 14.2 Implement Agent Brain Kafka consumer (`agent_brain/kafka_consumer.py`)
    - Implement `AgentBrainKafkaConsumer` with consumer group "mq-guardian-agent-brain"
    - Implement `start` — subscribe to "mq-telemetry-raw", "mq-topology-snapshot", "mq-human-feedback"
    - Implement `_on_rebalance` — commit offsets before rebalance, restore after, log to mq-audit-log
    - Implement `_process_message` — 3-retry logic, route to mq-dead-letter on 3rd failure with original payload, error, retry count, correlation_id
    - Implement `_handle_backpressure` — pause overloaded topic, process backlog, resume below threshold
    - Implement `reset_offset` — reset consumer offset for replay/recovery
    - Implement `get_consumer_lag` — lag per topic-partition, publish to Prometheus, alert on threshold
    - Implement `_commit_offsets_after_processing` — commit only after successful processing (at-least-once)
    - Implement `_retry_connection` — exponential backoff 1s initial, 60s max, publish connectivity alert
    - _Requirements: 14.9, 14.10, 14.11, 14.12, 14.13, 14.14, 14.15, 14.16, 20.1, 20.9, 20.10, 20.11_

  - [x] 14.3 Implement Agent Brain Kafka producer integration (`agent_brain/kafka_producer.py`)
    - Create producer wrapper for Agent Brain to publish to: mq-anomaly-detected, mq-approval-needed, mq-audit-log, mq-predictive-alert
    - Ensure correlation_id attached to every produced message
    - _Requirements: 14.11_

  - [x] 14.4 Write unit tests for Agent Brain state and Kafka integration
    - Test state model validation, consumer group config, retry logic, backpressure, dead-letter routing
    - _Requirements: 14.5, 14.9, 14.13, 14.14_

- [x] 15. Agent Brain — LangGraph workflow graphs
  - [x] 15.1 Implement Bootstrap workflow graph (`agent_brain/graph.py`)
    - Implement `AgentBrainGraph.build_bootstrap_graph` — define LangGraph StateGraph with nodes: ingest_csv → transform_topology → load_target_to_neo4j → detect_anomalies → compute_impact_analysis → generate_decision_report → request_human_approval → generate_iac_delta → create_github_pr → run_ansible_after_merge → persist_target_baseline
    - Wire conditional edges for human approval pause/resume
    - _Requirements: 14.1_

  - [x] 15.2 Implement Steady-State workflow graph (`agent_brain/graph.py`)
    - Implement `build_steady_state_graph` — define continuous loop: monitor_topology_via_kafka → detect_drift → (if drift) compute_impact_analysis → generate_decision_report → propose_remediation → request_human_approval → (on approval) generate_iac_delta → create_github_pr → run_ansible_after_merge → fold_changes_into_target_topology
    - Wire conditional edges for drift detection branching and approval decisions
    - _Requirements: 14.2_

  - [x] 15.3 Implement Agent Brain node functions (`agent_brain/graph.py`)
    - Implement `ingest_csv` — parse CSV, validate, publish snapshot to Event_Bus
    - Implement `transform_topology` — run TopologyTransformer
    - Implement `load_target_to_neo4j` — load target topology into Graph_Store
    - Implement `detect_anomalies` — detect rule-based anomalies, classify severity, publish to mq-anomaly-detected
    - Implement `detect_drift` — compare live vs target topology, classify drift type (configuration/structural/policy)
    - Implement `compute_impact_analysis` — compute blast radius, risk score, downstream deps, rollback plan
    - Implement `generate_decision_report` — generate Decision_Report with reasoning chain, alternatives, policy refs
    - Implement `request_human_approval` — publish to mq-approval-needed, pause until mq-human-feedback response
    - Implement `generate_iac_delta` — generate Terraform modules and Ansible playbooks
    - Implement `create_github_pr` — create PR with TF configs, variable files, plan output
    - Implement `run_ansible_after_merge` — execute Ansible playbooks after PR merge and TF apply
    - Implement `persist_target_baseline` — persist target topology as authoritative baseline
    - Implement `fold_changes_into_target` — update target-state with applied changes, verify compliance
    - _Requirements: 1.4, 4.7, 6.1, 6.2, 6.5, 6.6, 6.7, 7.1, 7.2, 7.3, 7.8, 14.1, 14.2, 14.4, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8_

  - [x] 15.4 Implement mode transition logic (`agent_brain/graph.py`)
    - Implement Bootstrap → Steady-State transition after successful bootstrap completion
    - Implement error handling: log to Audit_Store, notify Chatbot, halt for manual intervention on unrecoverable errors
    - Support concurrent processing of multiple independent topology change requests
    - _Requirements: 14.3, 14.6, 14.7_

  - [x] 15.5 Write unit tests for Agent Brain workflow graphs
    - Test bootstrap graph step ordering, steady-state loop, mode transition, error handling
    - Mock external dependencies (Neo4j, Kafka, IaC)
    - _Requirements: 14.1, 14.2, 14.3, 14.6_

- [x] 16. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 17. Terraform Module Generator
  - [~] 17.1 Implement Terraform module generator (`iac_engine/terraform_generator.py`)
    - Implement `TerraformModuleGenerator.generate_module` — generate separate TF module per MQ object type (queue_managers, queues_local, queues_remote, queues_transmission, channels)
    - Implement `generate_variable_files` — tfvars per environment (dev/staging/prod) with hostnames, ports, credential refs, resource limits
    - Implement `generate_remote_state_config` — S3/Azure Blob backend with state locking (DynamoDB/Azure Table)
    - Implement `generate_import_statements` — terraform import for existing unmanaged resources
    - Implement `generate_workspace_config` — workspace configs for multi-environment deployments
    - Implement `generate_output_definitions` — output definitions for cross-module references (QM endpoints, queue names, channel IDs)
    - Implement `validate` — run terraform validate, return (success, output)
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 8.7, 8.8, 8.12_

  - [~] 17.2 Write unit tests for Terraform module generator
    - Test module generation for each object type, variable file parameterization, remote state config, import statements
    - _Requirements: 8.1, 8.2, 8.3, 8.12_

- [ ] 18. Ansible Playbook Generator
  - [~] 18.1 Implement Ansible playbook generator (`iac_engine/ansible_generator.py`)
    - Implement `AnsiblePlaybookGenerator.generate_playbook` — playbooks for MQ object CRUD (create/update/delete) for QMs, queues, channels
    - Implement `generate_dynamic_inventory` — dynamic inventory grouped by QM, neighborhood, region
    - Implement `generate_roles` — reusable roles: qm_setup, queue_creation, channel_pair_creation, security_config
    - Implement `generate_vault_config` — Ansible Vault encryption for credentials and TLS certs
    - Implement `generate_handlers` — handlers for MQ service restart/reload on config changes
    - Implement `generate_callback_plugin` — callback plugin for real-time status reporting to Agent_Brain via mq-audit-log
    - Implement `syntax_check` — run ansible-playbook --syntax-check, return (success, output)
    - _Requirements: 8b.1, 8b.2, 8b.3, 8b.4, 8b.5, 8b.6, 8b.7, 8b.8, 8b.10_

  - [~] 18.2 Write unit tests for Ansible playbook generator
    - Test playbook generation for each action type, inventory grouping, role structure, vault config
    - _Requirements: 8b.1, 8b.2, 8b.3, 8b.10_

- [ ] 19. IaC Pipeline orchestration
  - [~] 19.1 Implement IaC pipeline (`iac_engine/pipeline.py`)
    - Implement `IaCPipeline.execute_terraform_plan` — dry-run, return plan output for operator review
    - Implement `create_github_pr` — create PR with TF configs, variable files, plan summary, change description
    - Implement `on_pr_merged` — trigger Terraform Enterprise apply on PR merge
    - Implement `execute_terraform_apply` — apply TF config; on failure: publish failure event, rollback, notify
    - Implement `execute_ansible_dry_run` — ansible check mode, return output for operator review
    - Implement `execute_ansible` — execute playbook; on failure: publish event, rollback, notify
    - Implement `compute_state_diff` — diff current TF state vs desired state from Graph_Store
    - _Requirements: 8.4, 8.5, 8.9, 8.10, 8.11, 8b.5, 8b.7, 8b.9_

  - [~] 19.2 Write unit tests for IaC pipeline
    - Test plan execution, PR creation, apply with failure/rollback, state diff
    - _Requirements: 8.4, 8.5, 8.11, 8b.9_

- [ ] 20. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 21. Human-in-the-Loop Approval Workflow
  - [~] 21.1 Implement HiTL approval workflow (`agent_brain/approval.py`)
    - Implement approval request publishing with change description, blast_radius, risk_score, decision_report, rollback_plan, unique approval_id
    - Implement approval response handling for all 6 decision types: APPROVE, REJECT, MODIFY, DEFER, ESCALATE, BATCH_APPROVE
    - Implement MODIFY flow: accept adjusted params, re-validate against Policy_Engine
    - Implement DEFER flow: record deferral with scheduled reminder timestamp, re-surface at reminder time
    - Implement ESCALATE flow: route to senior operator or CAB, log escalation to mq-audit-log
    - Implement BATCH_APPROVE flow: validate related requests, combined Policy_Engine validation
    - Implement SLA enforcement: configurable timeout (default 4h), auto-escalate on expiry
    - Implement multi-level approval: require 2+ approvers for high/critical risk or >10 QMs affected
    - Implement confidence scoring: analyze past decisions, flag high-confidence proposals
    - Ensure Agent_Brain does NOT proceed while approval is pending
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12_

  - [~] 21.2 Write unit tests for HiTL approval workflow
    - Test each decision type, SLA timeout, multi-level approval, batch validation, confidence scoring
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.9, 7.10_

- [ ] 22. Application Onboarding Automation
  - [~] 22.1 Implement application onboarding workflow (`agent_brain/onboarding.py`)
    - Implement onboarding request analysis: determine required MQ objects (queues, QMs, channels, producers, consumers)
    - Implement constraint validation of proposed objects via Policy_Engine
    - Implement IaC artifact generation (Terraform + Ansible) for new MQ infrastructure
    - Implement HiTL routing for onboarding proposals
    - Implement topology folding: merge new objects into target-state in Graph_Store, verify compliance
    - Implement audit event publishing for each onboarding action
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8_

  - [~] 22.2 Write unit tests for application onboarding
    - Test object determination, constraint validation, topology folding, audit trail
    - _Requirements: 19.1, 19.2, 19.6, 19.7_

- [ ] 23. Drift Detection and Remediation
  - [~] 23.1 Implement continuous drift detection (`agent_brain/drift.py`)
    - Implement embedded Kafka consumer for drift monitoring (mq-telemetry-raw, mq-topology-snapshot)
    - Implement configurable interval comparison (default 5 min) of live vs target topology
    - Implement drift classification: configuration_drift, structural_drift, policy_drift
    - Implement remediation proposal generation with correlation_id linking
    - Implement HiTL routing for drift remediations
    - Implement IaC generation and execution for approved remediations
    - Implement target-state update after successful remediation
    - Implement full audit trail with correlation_ids
    - _Requirements: 6.5, 6.6, 6.7, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8_

  - [~] 23.2 Write unit tests for drift detection and remediation
    - Test drift classification, remediation proposal, audit trail
    - _Requirements: 20.2, 20.3, 20.4, 20.8_

- [ ] 24. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 25. Chatbot Natural Language Interface
  - [~] 25.1 Implement Chatbot FastAPI service (`chatbot/app.py`)
    - Implement `MQGuardianChatbot` class with Chainlit + FastAPI integration
    - Implement `handle_topology_query` — retrieve from Graph_Store, respond within 5 seconds
    - Implement `handle_anomaly_query` — retrieve latest anomalies with severity, affected objects, remediations
    - Implement `handle_provisioning_command` — translate NL to structured request, publish to mq-provision-request
    - Implement `handle_approval_decision` — publish all 6 decision types to mq-human-feedback with approval_id
    - Implement `handle_what_if_query` — route to Decision Engine, return projected outcome
    - Implement `handle_sandbox_request` — create sandbox session for operator
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [~] 25.2 Create Backstage catalog entity for Chatbot
    - Create `catalog-info.yaml` registering Chatbot as a "Tool" entity in Backstage IDP Marketplace
    - _Requirements: 10.6_

  - [~] 25.3 Write unit tests for Chatbot handlers
    - Test topology query, anomaly query, provisioning command, approval decision publishing
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [ ] 26. REST API layer
  - [~] 26.1 Implement REST API endpoints (`api/routes.py`)
    - Implement POST `/api/v1/topology/ingest` — CSV ingestion (bootstrap or incremental)
    - Implement GET `/api/v1/topology/snapshot` — current topology with optional `?lob=` filter
    - Implement GET `/api/v1/topology/snapshot/{version}` — historical topology version
    - Implement GET `/api/v1/topology/export/csv` — export target topology as CSV
    - Implement GET `/api/v1/topology/export/dot` — export topology as DOT graph
    - Implement GET `/api/v1/topology/visualization` — topology visualization data
    - Implement GET `/api/v1/complexity` — complexity metrics with optional `?lob=` filter
    - Implement GET `/api/v1/complexity/compare` — as-is vs target comparison
    - Implement GET `/api/v1/anomalies` — list detected anomalies
    - Implement GET `/api/v1/drift` — list drift events
    - Implement GET/POST `/api/v1/approvals` — list pending approvals, submit decision
    - Implement POST `/api/v1/approvals/batch` — batch approve related requests
    - Implement GET `/api/v1/impact/{proposal_id}` — impact analysis for a proposal
    - Implement GET `/api/v1/capacity` and `/api/v1/capacity/report` — capacity planning data
    - Implement GET `/api/v1/optimization/recommendations` — optimization recommendations
    - Implement POST/DELETE `/api/v1/sandbox` endpoints — create, apply change, export, destroy
    - Implement GET `/api/v1/audit` — query audit records with filters
    - Implement POST `/api/v1/policies/evaluate` — evaluate policies against a change
    - Implement GET `/api/v1/decision-report/{id}` — get decision report
    - Implement POST `/api/v1/what-if` — evaluate hypothetical change
    - _Requirements: 1.2, 5.3, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 12.4, 15.1, 16.1, 16.2, 16.3, 22.7, 23.6, 25.1, 25.6, 26.3, 26.7_

  - [~] 26.2 Implement API authentication and authorization middleware
    - Implement OAuth 2.0 / mTLS authentication via corporate IdP
    - Implement OPA-based fine-grained authorization per LOB and role
    - Reject unauthenticated/unauthorized requests with appropriate HTTP codes, log to Audit_Store
    - _Requirements: 17.4, 17.5_

  - [~] 26.3 Write unit tests for REST API endpoints
    - Test each endpoint with valid/invalid requests, auth checks, LOB filtering
    - _Requirements: 5.3, 12.4, 17.4, 17.5_

- [ ] 27. Topology Visualization
  - [~] 27.1 Implement topology visualization (`api/visualization.py`)
    - Implement DOT graph export showing QMs as nodes, channels as edges, app associations labeled
    - Implement side-by-side comparison data: highlight added, removed, unchanged elements
    - _Requirements: 16.1, 16.2, 16.3_

  - [~] 27.2 Write unit tests for topology visualization
    - Test DOT export format, comparison highlighting
    - _Requirements: 16.1, 16.2, 16.3_

- [ ] 28. Multi-LOB Topology Governance
  - [~] 28.1 Implement LOB governance logic (`agent_brain/lob_governance.py`)
    - Implement LOB partitioning: assign QMs, queues, channels, apps to exactly one LOB
    - Implement LOB-level complexity metrics computation
    - Implement LOB-specific approval routing: single-LOB → LOB owner, cross-LOB → all affected LOB owners
    - Implement cross-LOB dependency tracking: identify cross-LOB channels and flows, flag in blast radius and Decision_Report
    - _Requirements: 26.1, 26.2, 26.3, 26.4, 26.5, 26.6_

  - [~] 28.2 Write unit tests for LOB governance
    - Test LOB assignment, LOB-level metrics, cross-LOB routing, dependency tracking
    - _Requirements: 26.1, 26.3, 26.4, 26.5, 26.6_

- [ ] 29. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 30. Monitoring Stack — Prometheus and Grafana
  - [~] 30.1 Implement Prometheus metrics and exporters (`monitoring/metrics.py`)
    - Implement Prometheus client metrics: consumer_lag, anomaly_count, terraform_apply_status, ansible_execution_status, approval_queue_depth, drift_events_count
    - Implement metrics endpoint for scraping
    - _Requirements: 11.6, 14.16_

  - [~] 30.2 Create Prometheus configuration (`monitoring/prometheus.yml`)
    - Configure scrape targets for all platform components
    - Define alert rules: consumer_lag_high, anomaly_critical, terraform_apply_failed
    - Configure alert notification channels (fire within 60 seconds of threshold breach)
    - _Requirements: 11.6_

  - [~] 30.3 Create Grafana dashboard definitions (`monitoring/dashboards/`)
    - Create "MQ Topology Overview" dashboard: Neo4j graph viz, complexity metrics as-is/target, comparison chart
    - Create "Drift & Anomaly Live" dashboard: real-time anomalies from mq-anomaly-detected, HiTL approval queue, drift timeline, severity distribution
    - Create "Predictive Maintenance" dashboard: Prophet forecast charts, capacity trend per QM, drift trend forecast
    - Create "IaC Pipeline Health" dashboard: TFE run status, GitHub PR lifecycle metrics, Ansible execution status
    - Create "Audit & Compliance" dashboard: full action log from mq-audit-log, policy violation history, approval decision history
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [~] 30.4 Create Grafana provisioning configuration (`monitoring/provisioning/`)
    - Configure datasource provisioning for Prometheus
    - Configure dashboard provisioning to auto-load all 5 dashboards
    - _Requirements: 11.1_

- [ ] 31. Audit and Compliance Traceability
  - [~] 31.1 Implement audit event publishing across all components
    - Ensure every significant action publishes to mq-audit-log: topology ingestion, anomaly detection, approval requests/decisions, IaC generation, PR creation, TF plan/apply, Ansible dry-run/execution, policy evaluations, impact analysis, decision reports
    - Ensure each audit event includes timestamp, correlation_id, actor, action_type, affected_objects, outcome
    - Ensure audit records are immutable (no modify/delete after persist)
    - Ensure 7-year retention policy configuration
    - _Requirements: 12.1, 12.2, 12.3, 12.5_

  - [~] 31.2 Write unit tests for audit traceability
    - Test audit event structure, immutability, correlation_id linkage
    - _Requirements: 12.1, 12.2, 12.5_

- [ ] 32. Security — TLS, authentication, encryption
  - [~] 32.1 Implement security configuration
    - Configure TLS 1.2+ between all components (Kafka SSL, Neo4j bolt+s, HTTPS for APIs)
    - Configure AES-256 encryption at rest for Neo4j volumes, Kafka log segments
    - Configure OAuth 2.0 via corporate IdP for API/Chatbot access
    - Configure mTLS for inter-service communication
    - Ensure no CSV data transmitted outside corporate network boundary
    - _Requirements: 1.6, 17.1, 17.2, 17.3, 17.4_

- [ ] 33. Docker Compose and Local Development
  - [~] 33.1 Create Docker Compose configuration (`docker-compose.yml`)
    - Define services: neo4j (5-enterprise with APOC), kafka (cp-kafka:7.5.0), schema-registry, agent-brain, chatbot, prometheus, grafana, policy-engine, iac-engine
    - Configure ports, volumes, environment variables, depends_on ordering
    - Configure Kafka with `KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"`
    - _Requirements: 18.1_

  - [~] 33.2 Create Kafka topic initialization script
    - Create script to pre-create all 9 Kafka topics with appropriate partitions and retention (7 days)
    - _Requirements: 13.1, 13.4_

  - [~] 33.3 Create Dockerfiles for custom services
    - Create `agent_brain/Dockerfile`
    - Create `chatbot/Dockerfile`
    - Create `policy_engine/Dockerfile`
    - Create `iac_engine/Dockerfile`
    - _Requirements: 18.1_

  - [~] 33.4 Create seed data and demo configuration
    - Create sample CSV files for bootstrap demonstration
    - Create configuration files for all components
    - Ensure complete workflow demonstrable from CSV ingestion through target topology export
    - _Requirements: 18.2, 18.3_

  - [~] 33.5 Write Docker Compose health check and startup validation
    - Verify all components healthy and communicating within 120 seconds of `docker-compose up`
    - _Requirements: 18.2_

- [ ] 34. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 35. Integration testing and end-to-end validation
  - [~] 35.1 Write integration tests for Bootstrap workflow
    - Test full bootstrap flow: CSV ingestion → transformation → Neo4j loading → anomaly detection → impact analysis → decision report → approval → IaC generation → target baseline persistence
    - Verify complexity reduction, constraint compliance, audit trail completeness
    - _Requirements: 1.1, 4.1, 4.7, 5.2, 6.1, 7.1, 8.1, 14.1_

  - [~] 35.2 Write integration tests for Steady-State workflow
    - Test drift detection → remediation proposal → approval → IaC execution → topology update
    - Test application onboarding flow end-to-end
    - _Requirements: 14.2, 19.1, 20.2, 20.6_

  - [~] 35.3 Write integration tests for Kafka event flow
    - Test end-to-end event flow across all 9 topics
    - Test correlation_id traceability across topics
    - Test dead-letter routing, backpressure handling, consumer rebalance
    - _Requirements: 13.1, 13.5, 14.9, 14.12, 14.13, 14.14_

  - [~] 35.4 Write integration tests for HiTL approval workflow
    - Test all 6 decision types end-to-end through Kafka
    - Test SLA timeout and auto-escalation
    - Test multi-level approval for high-risk changes
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.9, 7.10_

  - [~] 35.5 Write integration tests for sandbox workflow
    - Test sandbox creation, change simulation, composition, export to proposal, destruction
    - _Requirements: 25.1, 25.3, 25.4, 25.6, 25.7_

- [ ] 36. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 37. Hackathon Deliverables — Documentation and Artifacts
  - [~] 37.1 Create project README.md
    - Project overview, architecture summary, component list
    - Quick start guide (Docker Compose up, seed data, demo walkthrough)
    - Technology stack table
    - Repository structure explanation
    - Screenshots/diagrams of key features
    - _Requirements: All_

  - [~] 37.2 Create Architecture Decision Records (docs/adr/)
    - ADR-001: Why Neo4j for Graph Store
    - ADR-002: Why Kafka for Event Bus
    - ADR-003: Why LangGraph for Agent Brain
    - ADR-004: Why Sentinel + OPA dual policy engine
    - ADR-005: Why Terraform + Ansible split for IaC
    - ADR-006: Why Chainlit as standalone IDP plugin (not embedded in Web UI)
    - ADR-007: Why dual-mode operation (Bootstrap vs Steady-State)
    - ADR-008: Why Human-in-the-Loop for all changes
    - Each ADR: context, decision, rationale, consequences, alternatives considered
    - _Hackathon Deliverable: Design and decision documentation_

  - [~] 37.3 Create Complexity Algorithm Documentation (docs/complexity-algorithm.md)
    - Formal definition of the complexity metric formula
    - Weight factors for each component (channels, hops, fan-in/out, redundant objects)
    - Worked example with sample data showing as-is vs target calculation
    - Explanation of why this metric captures real MQ topology complexity
    - _Hackathon Deliverable: Complexity algorithm and calculation on input vs target dataset_

  - [~] 37.4 Create Target-State Topology Design Document (docs/target-state-design.md)
    - Explanation of transformation strategy and algorithm
    - How each MQ constraint is enforced (1 QM per app, remoteQ→xmitq→channel, deterministic naming, consumer→localQ)
    - How channels are introduced judiciously
    - How redundant objects are eliminated
    - How cycles and excessive fan-in/fan-out are avoided
    - Before/after topology diagrams
    - _Hackathon Deliverable: Design and decision documentation_

  - [~] 37.5 Create As-Is vs Target Complexity Analysis Report (docs/complexity-report.md)
    - Template for auto-generated complexity comparison report
    - Script to generate the report from any input CSV dataset
    - Includes: total channels (before/after), avg routing hops (before/after), fan-in/fan-out ratios, redundant objects eliminated, composite score improvement percentage
    - Visualization-ready data (charts/tables)
    - _Hackathon Deliverable: As-is vs target complexity analysis_

  - [~] 37.6 Create Topology Visualization Documentation (docs/topology-visualization.md)
    - How to generate DOT graph visualizations
    - How to render side-by-side as-is vs target comparisons
    - How to interpret the visualizations (node colors, edge styles, labels)
    - Sample rendered visualizations
    - _Hackathon Deliverable: Topology visualizations_

  - [~] 37.7 Create MQ Guardian Agent Documentation (docs/agent-guide.md)
    - Agent capabilities overview (dual-mode, drift detection, onboarding, optimization)
    - How the agent discovers QMs, queues, channels
    - How the agent identifies producer/consumer relationships
    - How the agent surfaces anomalies and redundant objects
    - How the agent explains its decisions (Decision Reports)
    - How Human-in-the-Loop works (approve/reject/modify/defer/escalate/batch)
    - _Hackathon Deliverable: Intelligent MQ Inventory & Target-State Agent_

  - [~] 37.8 Create API Reference Documentation (docs/api-reference.md)
    - Full REST API reference with all endpoints, request/response schemas, examples
    - Authentication requirements
    - Error codes and handling
    - _Requirements: 5.3, 12.4, 22.7, 23.6, 26.7_

  - [~] 37.9 Create Kafka Event Schema Documentation (docs/kafka-schemas.md)
    - All 9 topic definitions with purpose, schema, example messages
    - Event flow diagrams showing which components produce/consume each topic
    - Correlation ID tracing guide
    - _Requirements: 13.1, 13.2, 13.5_

  - [~] 37.10 Create Deployment and Operations Guide (docs/operations-guide.md)
    - Docker Compose local deployment instructions
    - Production deployment architecture
    - Monitoring and alerting setup (Prometheus + Grafana)
    - Troubleshooting guide
    - Security configuration (TLS, auth, encryption)
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 18.1, 18.2_

  - [~] 37.11 Create Hackathon Submission Checklist (docs/submission-checklist.md)
    - Checklist mapping each hackathon deliverable to its location in the repo
    - Target-state topology CSV → output/target_topology.csv
    - Complexity algorithm → docs/complexity-algorithm.md
    - Complexity analysis report → docs/complexity-report.md
    - Topology visualizations → output/visualizations/
    - Design and decision documentation → docs/adr/ + docs/target-state-design.md
    - Agent documentation → docs/agent-guide.md
    - _Hackathon Deliverable: Complete submission package_

- [ ] 38. Sample Output Generation
  - [~] 38.1 Create sample CSV input dataset for demonstration
    - Generate a realistic sample MQ topology CSV (smaller scale, ~100-200 rows) that mimics the structure of the real 12-14K row dataset
    - Include queue managers, queues, channels, applications, producer/consumer relationships, neighborhoods
    - _Requirements: 18.3_

  - [~] 38.2 Create sample target-state CSV output
    - Run the transformer against the sample input to produce a target-state CSV
    - Verify all MQ constraints are satisfied in the output
    - _Hackathon Deliverable: Target-state topology dataset in CSV format identical to input structure_

  - [~] 38.3 Generate sample complexity analysis report
    - Run complexity analyzer on sample input vs output
    - Generate the comparison report with all metrics
    - _Hackathon Deliverable: As-is vs target complexity analysis_

  - [~] 38.4 Generate sample topology visualizations
    - Generate DOT graph for sample as-is topology
    - Generate DOT graph for sample target topology
    - Generate side-by-side comparison visualization
    - Render to PNG/SVG for inclusion in documentation
    - _Hackathon Deliverable: Topology visualizations_

- [ ] 39. Final Documentation Review and Polish
  - Review all documentation for completeness, accuracy, and presentation quality
  - Ensure all hackathon deliverables are accounted for
  - Ensure README provides a clear, impressive first impression
  - Verify all diagrams render correctly

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at logical boundaries
- All code is Python; key frameworks: LangGraph, LangChain, Confluent Kafka, Neo4j, FastAPI, Chainlit, Prophet, Pydantic, Prometheus client
- Docker Compose enables fully local development with no external dependencies
- The implementation order ensures no orphaned code — each layer builds on the previous
