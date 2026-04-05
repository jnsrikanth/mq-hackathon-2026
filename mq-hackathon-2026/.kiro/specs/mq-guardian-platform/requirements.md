# Requirements Document

## Introduction

The MQ Guardian Platform is an enterprise-grade IBM MQ topology management system designed for large regulated banks. It operates in two modes: Bootstrap_Mode performs a one-time ingestion of legacy MQ environment data (CSV of 12-14K rows), transforms it into a compliant target-state topology, and loads it into a graph database (Neo4j). Once the target state is established, the platform transitions to Steady_State_Mode where an AI agent (Agent_Brain), built on Tachyon_Studio and using LangGraph/LangChain patterns, continuously monitors the topology for drift, handles new application onboarding, and proposes remediations — all through Human-in-the-Loop approval. The platform is policy-driven, GitOps-first, and fully auditable. It integrates an event bus (Kafka) with an embedded consumer for real-time monitoring, Infrastructure-as-Code pipelines (Terraform + Ansible), a chatbot (Chainlit), and monitoring dashboards (Grafana). The platform features an Explainable Decision Engine, Topology Impact Analysis, Capacity Planning, Optimization Recommendations, Change Simulation Sandbox, and Multi-LOB Governance capabilities.

## Glossary

- **Platform**: The MQ Guardian Platform system as a whole
- **Transformer**: The existing Python script that converts As-Is CSV topology data into a Target CSV topology satisfying all MQ constraints; runs primarily during Bootstrap_Mode
- **Graph_Store**: The Neo4j graph database used for persistent topology storage, querying, and validation
- **Agent_Brain**: The LangGraph + LangChain-based central intelligence component, built and deployed on Tachyon_Studio, that orchestrates the workflow, detects anomalies, monitors for drift, and coordinates Human-in-the-Loop approvals; operates in Bootstrap_Mode and Steady_State_Mode
- **Event_Bus**: The Confluent Kafka cluster providing decoupled, real-time event flow across platform components via defined topics
- **IaC_Engine**: The Terraform Enterprise + Ansible pipeline that provisions and configures MQ infrastructure via GitOps (GitHub PR → TFE → Ansible)
- **Terraform_Module_Generator**: The sub-component of the IaC_Engine responsible for generating Terraform modules, variable files, state configuration, import statements, and output definitions for MQ infrastructure
- **Ansible_Playbook_Generator**: The sub-component of the IaC_Engine responsible for generating Ansible playbooks, inventory files, roles, vault configurations, and handlers for MQ object configuration
- **Policy_Engine**: The Sentinel + OPA policy enforcement layer that validates topology changes against compliance, security, and MQ constraint rules
- **Chatbot**: The Chainlit + LangGraph-powered natural language interface, deployed as a standalone FastAPI service and registered as a Backstage IDP Marketplace plugin
- **Monitoring_Stack**: The Prometheus + Grafana monitoring and alerting subsystem providing 5 production dashboards and predictive alerts
- **Web_UI**: The existing dashboard for visual As-Is vs Target comparison (unchanged by this platform)
- **Audit_Store**: The combination of Kafka audit topic, Neo4j, and Grafana providing full action traceability
- **Queue_Manager (QM)**: An IBM MQ queue manager that hosts queues and channels
- **Application (App)**: A software application that produces or consumes messages via IBM MQ
- **Channel**: A sender/receiver pair enabling communication between two queue managers, named deterministically as fromQM.toQM / toQM.fromQM
- **Local_Queue**: A queue residing on a queue manager from which consumers read messages
- **Remote_Queue (remoteQ)**: A local queue with remote queue attributes enabled, used by producers to route messages to another queue manager via a transmission queue
- **Transmission_Queue (xmitq)**: A special local queue used to stage messages for transmission to a remote queue manager via a channel
- **Complexity_Metric**: A quantitative score measuring topology complexity based on channel count, routing hops, fan-in/fan-out patterns, and redundant objects
- **HiTL**: Human-in-the-Loop — a workflow pattern requiring explicit human approval before executing topology changes
- **GitOps**: A practice where infrastructure changes are managed through Git pull requests, providing version control and auditability
- **Topology_Snapshot**: A point-in-time representation of the MQ environment including all queue managers, queues, channels, and application relationships
- **Anomaly**: A detected deviation from expected topology patterns, policy violations, or predicted future issues
- **IDP**: Internal Developer Platform (Backstage-based)
- **Tachyon_Studio**: Wells Fargo's internal agentic development platform that exposes various LLMs for developing and deploying agentic applications; serves as the runtime platform for the Agent_Brain
- **Bootstrap_Mode**: The one-time operational mode in which the Platform ingests the full legacy CSV dataset (up to 12-14K rows), transforms it into the compliant target-state topology, and loads it into the Graph_Store
- **Steady_State_Mode**: The continuous operational mode following Bootstrap_Mode in which the Agent_Brain monitors the target-state topology for drift, handles new application onboarding, and proposes remediations through HiTL approval
- **Drift**: A deviation of the current MQ topology from the established compliant target-state topology, detected by comparing live topology events against the stored target state in the Graph_Store
- **Correlation_ID**: A unique identifier attached to every event produced by the Agent_Brain, enabling end-to-end traceability of a single logical operation across multiple Kafka topics and platform components
- **Dead_Letter_Queue (DLQ)**: A dedicated Kafka topic where messages that fail processing after exhausting retry attempts are routed for manual inspection and recovery
- **Consumer_Group**: A Kafka consumer group identifier used by the Agent_Brain to coordinate partition assignment, offset management, and rebalancing across multiple consumer instances
- **Backpressure**: A flow control mechanism that throttles event production or consumption when downstream processing capacity is exceeded
- **Blast_Radius**: The set of applications, queues, channels, and queue managers that would be directly or transitively affected by a proposed topology change
- **Risk_Score**: A quantitative assessment (low, medium, high, critical) of the potential impact of a proposed topology change, based on the Blast_Radius, object criticality, and historical change outcomes
- **Decision_Report**: A structured document generated by the Explainable Decision Engine for each topology change proposal, containing the reasoning chain, alternatives considered, and policy references
- **Sandbox_Mode**: An operational mode in which proposed topology changes are simulated against a copy of the topology graph without affecting the production target-state topology
- **LOB (Line_of_Business)**: An organizational unit within the enterprise that owns a subset of applications and MQ infrastructure; used for topology partitioning and delegated governance
- **Approval_SLA**: A configurable time limit within which a human operator must respond to an approval request before auto-escalation is triggered

## Requirements

### Requirement 1: CSV Topology Ingestion (Bootstrap and Incremental)

**User Story:** As a platform operator, I want to perform a one-time bulk ingestion of legacy CSV datasets during Bootstrap_Mode and support incremental CSV ingestion for new application onboarding during Steady_State_Mode, so that the platform can establish and maintain the target-state topology.

#### Acceptance Criteria

1. WHEN a full set of CSV files containing queue managers, queues, channels, applications, and producer/consumer relationships is provided during Bootstrap_Mode, THE Platform SHALL parse all CSV files into structured in-memory data objects within 30 seconds for datasets up to 14,000 rows per file.
2. WHEN an incremental CSV file containing new application onboarding data is provided during Steady_State_Mode, THE Platform SHALL parse the incremental CSV file and merge the new objects into the existing target-state topology in the Graph_Store.
3. WHEN a CSV file contains malformed rows or missing required columns, THE Platform SHALL reject the file, return a descriptive error message identifying the row and column, and halt ingestion for that file.
4. WHEN CSV files are successfully parsed, THE Platform SHALL publish a complete Topology_Snapshot event to the "mq-topology-snapshot" Event_Bus topic.
5. THE Platform SHALL accept CSV files containing queue manager identifiers, queue definitions with owning queue manager references, application identifiers with producer/consumer role designations, and optional metadata such as neighborhood or region.
6. WHEN CSV data is ingested, THE Platform SHALL NOT transmit any CSV data outside the corporate network boundary.

### Requirement 2: Graph Store Loading and Persistence

**User Story:** As a platform operator, I want the MQ topology stored in a graph database, so that I can query relationships and validate constraints efficiently.

#### Acceptance Criteria

1. WHEN a Topology_Snapshot event is received from the Event_Bus, THE Graph_Store SHALL create or update nodes for all queue managers, queues, applications, and channels, and create edges representing ownership, connectivity, and producer/consumer relationships.
2. WHEN the Graph_Store loads a Topology_Snapshot, THE Graph_Store SHALL preserve all original attributes from the CSV data as node and edge properties.
3. WHEN a new Topology_Snapshot is loaded, THE Graph_Store SHALL retain the previous snapshot as a versioned historical record.
4. THE Graph_Store SHALL support Cypher queries for traversing queue manager connectivity, application dependencies, and channel routing paths.

### Requirement 3: MQ Constraint Enforcement

**User Story:** As a compliance officer, I want all MQ topology constraints enforced automatically, so that the target topology is always valid.

#### Acceptance Criteria

1. THE Policy_Engine SHALL enforce that each Application connects to exactly one Queue_Manager (1 QM per AppID rule).
2. THE Policy_Engine SHALL enforce that data producers write only to a Remote_Queue within their own Queue_Manager.
3. THE Policy_Engine SHALL enforce that each Remote_Queue leverages a Transmission_Queue to send messages via server channels to the destination Queue_Manager.
4. THE Policy_Engine SHALL enforce that channels between queue managers use deterministic naming pairs following the pattern "fromQM.toQM" for the sender and "toQM.fromQM" for the receiver.
5. THE Policy_Engine SHALL enforce that consumers read only from Local_Queues on their own Queue_Manager.
6. WHEN a proposed topology change violates any MQ constraint, THE Policy_Engine SHALL reject the change and return a structured error identifying the violated constraint, the offending objects, and a remediation suggestion.

### Requirement 4: Topology Transformation (Bootstrap)

**User Story:** As a platform operator, I want the platform to perform a one-time transformation of the legacy MQ topology into a simplified, compliant target-state architecture during Bootstrap_Mode, so that complexity is reduced while maintaining application capabilities.

#### Acceptance Criteria

1. WHEN the Agent_Brain initiates a transformation during Bootstrap_Mode, THE Transformer SHALL produce a target-state topology that reduces the total number of channels compared to the as-is topology.
2. WHEN the Transformer produces a target topology, THE Transformer SHALL eliminate redundant or unused MQ objects (queues, channels, queue managers with no connected applications).
3. WHEN the Transformer produces a target topology, THE Transformer SHALL minimize routing hops between communicating applications.
4. THE Transformer SHALL produce target-state topology output in CSV format with a structure identical to the input CSV format.
5. WHEN the Transformer produces a target topology, THE Transformer SHALL avoid introducing cycles in the channel routing graph.
6. WHEN the Transformer produces a target topology, THE Transformer SHALL avoid excessive fan-in or fan-out patterns where a single queue manager has a disproportionately high number of inbound or outbound channels relative to the topology average.
7. WHEN the Transformer completes the Bootstrap_Mode transformation, THE Platform SHALL persist the resulting target-state topology in the Graph_Store as the authoritative baseline for Steady_State_Mode drift detection.

### Requirement 5: Complexity Analysis

**User Story:** As a platform operator, I want a quantitative complexity metric comparing as-is and target topologies, so that I can demonstrate measurable improvement.

#### Acceptance Criteria

1. THE Platform SHALL compute a Complexity_Metric for any given Topology_Snapshot based on total channel count, average routing hops between communicating application pairs, fan-in/fan-out ratios per queue manager, and count of redundant MQ objects.
2. WHEN both as-is and target Topology_Snapshots are available, THE Platform SHALL produce a comparative complexity analysis showing the absolute and percentage change for each metric component.
3. THE Platform SHALL expose the Complexity_Metric via a REST API endpoint returning a JSON response.
4. WHEN the target topology Complexity_Metric is not lower than the as-is topology Complexity_Metric, THE Platform SHALL flag the transformation result as "no improvement" in the audit log.

### Requirement 6: Anomaly Detection and Continuous Drift Monitoring

**User Story:** As a platform operator, I want the platform to detect topology anomalies during Bootstrap_Mode and continuously monitor for drift from the compliant target state during Steady_State_Mode, so that I can proactively address problems and maintain compliance.

#### Acceptance Criteria

1. WHEN the Agent_Brain analyzes a Topology_Snapshot during Bootstrap_Mode, THE Agent_Brain SHALL detect rule-based anomalies including policy violations, orphaned queues, disconnected queue managers, and constraint breaches.
2. WHEN the Agent_Brain detects an anomaly, THE Agent_Brain SHALL publish an event to the "mq-anomaly-detected" Event_Bus topic containing the anomaly type, severity, affected objects, and a recommended remediation.
3. WHEN historical topology data is available, THE Agent_Brain SHALL use Prophet ML forecasting to generate predictive alerts for capacity issues and topology drift, publishing them to the "mq-predictive-alert" Event_Bus topic.
4. WHEN an anomaly is detected, THE Agent_Brain SHALL classify the anomaly severity as "critical", "warning", or "informational".
5. WHILE operating in Steady_State_Mode, THE Agent_Brain SHALL continuously consume topology change events from the Event_Bus via its embedded Kafka Consumer and compare the current topology state against the established target-state topology in the Graph_Store at regular configurable intervals.
6. WHEN the Agent_Brain detects Drift between the current topology and the target-state topology during Steady_State_Mode, THE Agent_Brain SHALL publish a drift event to the "mq-anomaly-detected" Event_Bus topic containing the drift type, affected objects, delta description, and a proposed remediation to restore compliance.
7. WHEN a drift remediation is proposed, THE Agent_Brain SHALL route the remediation through the HiTL approval workflow before executing any corrective action via the IaC_Engine.

### Requirement 7: Human-in-the-Loop Approval Workflow (Enriched)

**User Story:** As a compliance officer, I want a rich approval workflow supporting approve, reject, modify, defer, escalate, and batch approval actions with SLA enforcement and multi-level approval for high-risk changes, so that topology changes receive appropriate human oversight proportional to their risk.

#### Acceptance Criteria

1. WHEN the Agent_Brain proposes a topology change or remediation, THE Agent_Brain SHALL publish an approval request to the "mq-approval-needed" Event_Bus topic containing the change description, Blast_Radius, Risk_Score, Decision_Report, rollback plan, and a unique approval_id, and pause execution until a response is received.
2. WHEN a human operator submits an APPROVE decision via the Chatbot or a direct API call, THE Platform SHALL publish the decision to the "mq-human-feedback" Event_Bus topic and THE Agent_Brain SHALL proceed with the approved change by triggering the IaC_Engine.
3. WHEN a human operator submits a REJECT decision via the Chatbot or a direct API call, THE Platform SHALL publish the decision to the "mq-human-feedback" Event_Bus topic and THE Agent_Brain SHALL cancel the proposed change and log the rejection reason in the Audit_Store.
4. WHEN a human operator submits a MODIFY decision via the Chatbot or a direct API call, THE Platform SHALL accept the operator's adjusted change parameters, re-validate the modified proposal against the Policy_Engine, and publish the modified decision to the "mq-human-feedback" Event_Bus topic for the Agent_Brain to execute the adjusted change.
5. WHEN a human operator submits a DEFER decision via the Chatbot or a direct API call, THE Platform SHALL record the deferral with a scheduled reminder timestamp and publish the deferral to the "mq-human-feedback" Event_Bus topic, and THE Agent_Brain SHALL re-surface the approval request at the scheduled reminder time.
6. WHEN a human operator submits an ESCALATE decision via the Chatbot or a direct API call, THE Platform SHALL route the approval request to the designated senior operator or change advisory board and publish the escalation to the "mq-audit-log" Event_Bus topic.
7. WHEN a human operator selects BATCH APPROVE for multiple related pending approval requests, THE Platform SHALL validate that all selected requests are logically related (same drift event or onboarding request), apply a single combined Policy_Engine validation, and publish a batch approval decision to the "mq-human-feedback" Event_Bus topic.
8. WHILE an approval request is pending, THE Agent_Brain SHALL NOT proceed with the proposed change.
9. THE Platform SHALL enforce a configurable Approval_SLA for each approval request with a default timeout of 4 hours, and WHEN the Approval_SLA expires without a human response, THE Platform SHALL auto-escalate the request to the next approval tier and publish an escalation event to the "mq-audit-log" Event_Bus topic.
10. WHEN a proposed change has a Risk_Score of "high" or "critical" or affects more than 10 queue managers, THE Platform SHALL require multi-level approval from at least two designated approvers before the Agent_Brain proceeds with execution.
11. WHEN the Agent_Brain accumulates a history of approval and rejection decisions, THE Agent_Brain SHALL analyze past decision patterns to compute a confidence score for future proposals, and WHEN the confidence score exceeds a configurable threshold, THE Agent_Brain SHALL flag the proposal as "high-confidence" in the approval request to assist human decision-making.
12. THE Platform SHALL include a unique approval_id, Correlation_ID, and timestamp in each approval request to correlate requests with responses across the full event chain.

### Requirement 8: Terraform Infrastructure-as-Code Pipeline

**User Story:** As a platform engineer, I want the IaC_Engine to generate granular, environment-aware Terraform modules for each MQ object type with remote state management, plan preview, import support, and workspace management, so that all MQ infrastructure changes are version-controlled, auditable, and safely deployable across environments.

#### Acceptance Criteria

1. WHEN the Agent_Brain receives human approval for a topology change, THE Terraform_Module_Generator SHALL generate a separate Terraform module for each affected MQ object type including queue managers, queues (local, remote, transmission), and channels.
2. WHEN the Terraform_Module_Generator generates modules, THE Terraform_Module_Generator SHALL generate Terraform variable files parameterized per target environment (dev, staging, prod) with environment-specific values for hostnames, ports, credentials references, and resource limits.
3. WHEN the Terraform_Module_Generator generates modules, THE Terraform_Module_Generator SHALL generate Terraform remote state configuration targeting S3 or Azure Blob Storage with state locking enabled via DynamoDB or Azure Table Storage.
4. WHEN a Terraform configuration delta is generated, THE IaC_Engine SHALL execute a Terraform plan (dry-run) and present the plan output to the operator via the Chatbot before proceeding to apply.
5. WHEN the operator confirms the Terraform plan, THE IaC_Engine SHALL create a GitHub Pull Request containing the Terraform configuration files, variable files, plan output summary, and a description of the change.
6. WHEN existing MQ infrastructure is discovered that is not yet managed by Terraform, THE Terraform_Module_Generator SHALL generate Terraform import statements to bring the existing resources under Terraform state management.
7. THE Terraform_Module_Generator SHALL generate Terraform workspace configurations to support multi-environment deployments, with each workspace corresponding to a target environment (dev, staging, prod).
8. WHEN the Terraform_Module_Generator generates modules, THE Terraform_Module_Generator SHALL generate Terraform output definitions for cross-module references including queue manager endpoints, queue names, and channel identifiers.
9. WHEN the Agent_Brain requests a state comparison, THE IaC_Engine SHALL execute a diff between the current Terraform state and the desired state derived from the Graph_Store target topology, and return a structured delta report.
10. WHEN a GitHub Pull Request is merged, THE IaC_Engine SHALL trigger Terraform Enterprise to apply the configuration.
11. IF the Terraform apply fails, THEN THE IaC_Engine SHALL publish a failure event to the "mq-audit-log" Event_Bus topic, roll back the partial change, and notify the operator via the Chatbot.
12. FOR ALL valid Terraform configurations generated by the Terraform_Module_Generator, running "terraform validate" SHALL produce zero errors (round-trip validation property).

### Requirement 8b: Ansible Configuration Management Pipeline

**User Story:** As a platform engineer, I want the IaC_Engine to generate comprehensive Ansible playbooks, dynamic inventories, reusable roles, and vault-secured configurations for MQ object CRUD operations, so that MQ objects are configured consistently and securely on target queue managers after Terraform provisioning.

#### Acceptance Criteria

1. WHEN Terraform Enterprise successfully applies the infrastructure configuration, THE Ansible_Playbook_Generator SHALL generate Ansible playbooks for MQ object CRUD operations including create, update, and delete actions for queue managers, queues (local, remote, transmission), and channels.
2. WHEN the Ansible_Playbook_Generator generates playbooks, THE Ansible_Playbook_Generator SHALL generate dynamic Ansible inventory files based on the target topology in the Graph_Store, grouping hosts by queue manager, neighborhood, and region.
3. THE Ansible_Playbook_Generator SHALL generate reusable Ansible roles for common MQ configuration patterns including queue manager setup, queue creation, channel pair creation, and security configuration.
4. WHEN the Ansible_Playbook_Generator generates playbooks that reference MQ credentials or TLS certificates, THE Ansible_Playbook_Generator SHALL use Ansible Vault to encrypt sensitive values and generate vault password file references.
5. WHEN the IaC_Engine is ready to execute Ansible playbooks, THE IaC_Engine SHALL first execute the playbooks in Ansible check mode (dry-run) and present the check mode output to the operator via the Chatbot before actual execution.
6. WHEN the Ansible_Playbook_Generator generates playbooks that modify queue manager configurations, THE Ansible_Playbook_Generator SHALL generate Ansible handlers for MQ service restart or reload operations triggered by configuration changes.
7. WHEN the IaC_Engine is ready to execute Ansible playbooks, THE IaC_Engine SHALL validate the playbook syntax using "ansible-playbook --syntax-check" before execution and reject playbooks with syntax errors.
8. THE Ansible_Playbook_Generator SHALL generate Ansible callback plugin configurations that report real-time execution status (task start, task complete, task failed) back to the Agent_Brain via the "mq-audit-log" Event_Bus topic.
9. IF the Ansible execution fails, THEN THE IaC_Engine SHALL publish a failure event to the "mq-audit-log" Event_Bus topic, attempt to roll back the failed configuration change, and notify the operator via the Chatbot.
10. FOR ALL valid Ansible playbooks generated by the Ansible_Playbook_Generator, running "ansible-playbook --syntax-check" SHALL produce zero errors (round-trip validation property).

### Requirement 9: Policy-as-Code Enforcement

**User Story:** As a compliance officer, I want policies enforced as code before any infrastructure change, so that non-compliant changes are blocked automatically.

#### Acceptance Criteria

1. THE Policy_Engine SHALL evaluate Sentinel policies including "exactly_one_qm_per_app", "secure_by_default", and "neighbourhood_aligned" against every proposed topology change before a GitHub Pull Request is created.
2. THE Policy_Engine SHALL evaluate OPA policies for fine-grained access control and data validation checks before a GitHub Pull Request is created.
3. WHEN a proposed change violates any Sentinel or OPA policy, THE Policy_Engine SHALL block the change, return the list of violated policies with descriptions, and log the violation in the Audit_Store.
4. WHEN all policies pass, THE Policy_Engine SHALL attach a policy compliance attestation to the GitHub Pull Request.

### Requirement 10: Chatbot Natural Language Interface

**User Story:** As a platform operator, I want to interact with the platform using natural language, so that I can query topology state, approve changes, and investigate anomalies without navigating multiple tools.

#### Acceptance Criteria

1. WHEN a user sends a natural language query about the current topology state, THE Chatbot SHALL retrieve relevant data from the Graph_Store and return a human-readable response within 5 seconds.
2. WHEN a user requests anomaly details via the Chatbot, THE Chatbot SHALL retrieve the latest anomalies from the Agent_Brain and present them with severity, affected objects, and recommended remediations.
3. WHEN a user issues a provisioning command via the Chatbot, THE Chatbot SHALL translate the command into a structured request and publish it to the "mq-provision-request" Event_Bus topic.
4. WHEN a user submits an approval, rejection, modification, deferral, escalation, or batch approval decision via the Chatbot, THE Chatbot SHALL publish the decision to the "mq-human-feedback" Event_Bus topic with the corresponding approval_id and decision type.
5. THE Chatbot SHALL be deployed as a standalone FastAPI service, separate from the existing Web_UI.
6. THE Chatbot SHALL be registered in the Backstage catalog as a "Tool" entity, accessible from the IDP Marketplace.

### Requirement 11: Monitoring and Dashboards

**User Story:** As a platform operator, I want production dashboards showing topology health, anomalies, pipeline status, and audit trails, so that I have full operational visibility.

#### Acceptance Criteria

1. THE Monitoring_Stack SHALL provide a "MQ Topology Overview" Grafana dashboard displaying the Neo4j graph visualization and Complexity_Metric values for both as-is and target topologies.
2. THE Monitoring_Stack SHALL provide a "Drift & Anomaly Live" Grafana dashboard displaying real-time anomalies from the "mq-anomaly-detected" topic and the current HiTL approval queue.
3. THE Monitoring_Stack SHALL provide a "Predictive Maintenance" Grafana dashboard displaying Prophet ML forecast charts for capacity and topology drift trends.
4. THE Monitoring_Stack SHALL provide an "IaC Pipeline Health" Grafana dashboard displaying Terraform Enterprise run status and GitHub Pull Request lifecycle metrics.
5. THE Monitoring_Stack SHALL provide an "Audit & Compliance" Grafana dashboard displaying the full action log from the "mq-audit-log" topic and policy violation history.
6. WHEN a Prometheus metric exceeds a configured threshold, THE Monitoring_Stack SHALL fire an alert to the configured notification channel within 60 seconds.

### Requirement 12: Audit and Compliance Traceability

**User Story:** As an auditor, I want a complete, immutable record of all platform actions, so that I can trace every topology change from detection through approval to execution.

#### Acceptance Criteria

1. THE Platform SHALL publish an audit event to the "mq-audit-log" Event_Bus topic for every significant action including: topology ingestion, anomaly detection, approval requests, approval decisions (approve, reject, modify, defer, escalate, batch approve), IaC generation, PR creation, Terraform plan, Terraform apply, Ansible dry-run, Ansible execution, policy evaluations, impact analysis, and decision reports.
2. WHEN an audit event is published, THE Audit_Store SHALL persist the event in the Graph_Store with a timestamp, Correlation_ID, actor identifier, action type, affected objects, and outcome.
3. THE Audit_Store SHALL retain all audit records for a minimum of 7 years to satisfy regulatory requirements.
4. THE Platform SHALL provide a REST API endpoint to query audit records filtered by time range, action type, actor, affected object, and Correlation_ID.
5. WHEN an audit record is persisted, THE Audit_Store SHALL make the record immutable — subsequent operations SHALL NOT modify or delete existing audit records.

### Requirement 13: Event Bus Topic Management

**User Story:** As a platform engineer, I want well-defined Kafka topics with clear schemas, so that all components communicate reliably and events are traceable.

#### Acceptance Criteria

1. THE Event_Bus SHALL provide the following 9 topics: "mq-telemetry-raw", "mq-topology-snapshot", "mq-anomaly-detected", "mq-provision-request", "mq-approval-needed", "mq-human-feedback", "mq-audit-log", "mq-predictive-alert", and "mq-dead-letter".
2. THE Event_Bus SHALL enforce a defined Avro or JSON schema for each topic to validate message structure on produce.
3. IF a message fails schema validation, THEN THE Event_Bus SHALL reject the message and return a schema validation error to the producing component.
4. THE Event_Bus SHALL retain messages for a minimum of 7 days to support replay and debugging.
5. THE Event_Bus SHALL include a Correlation_ID field in every message schema to enable end-to-end traceability across topics.

### Requirement 14: Agent Brain Orchestration (Dual-Mode with Deep Kafka Integration)

**User Story:** As a platform operator, I want an intelligent agent built on Tachyon_Studio that operates in two modes — Bootstrap_Mode for initial topology transformation and Steady_State_Mode for continuous monitoring and remediation — with deep Kafka producer/consumer integration including dedicated consumer groups, correlation-based traceability, backpressure handling, dead-letter routing, and multi-topic consumption, so that the platform handles both initial setup and ongoing operations reliably at scale.

#### Acceptance Criteria

1. WHILE operating in Bootstrap_Mode, THE Agent_Brain SHALL execute the following workflow steps in order: ingest_csv → transform_topology → load_target_to_neo4j → detect_anomalies → compute_impact_analysis → generate_decision_report → request_human_approval → generate_iac_delta → create_github_pr → run_ansible_after_merge → persist_target_baseline.
2. WHILE operating in Steady_State_Mode, THE Agent_Brain SHALL execute a continuous loop: monitor_topology_via_kafka → detect_drift → (if drift detected) compute_impact_analysis → generate_decision_report → propose_remediation → request_human_approval → (on approval) generate_iac_delta → create_github_pr → run_ansible_after_merge → fold_changes_into_target_topology.
3. WHEN the Agent_Brain completes Bootstrap_Mode successfully, THE Agent_Brain SHALL transition to Steady_State_Mode and begin continuous monitoring via its embedded Kafka Consumer.
4. WHEN the Agent_Brain transitions between workflow steps, THE Agent_Brain SHALL publish a state transition event to the "mq-audit-log" Event_Bus topic with the Correlation_ID of the originating operation.
5. THE Agent_Brain SHALL maintain its state using a Pydantic AgentState model containing: current_mode (bootstrap or steady_state), snapshot, graph_data, anomalies, drift_events, proposed_fixes, human_approval_needed flag, approval_id, correlation_id, impact_analysis, decision_report, audit_log, and timestamp.
6. WHEN the Agent_Brain encounters an unrecoverable error at any workflow step, THE Agent_Brain SHALL log the error to the Audit_Store, publish a notification to the Chatbot, and halt the workflow for manual intervention.
7. THE Agent_Brain SHALL support concurrent processing of multiple independent topology change requests without state interference between requests.
8. THE Agent_Brain SHALL be developed and deployed using Tachyon_Studio's LLM exposure layer, leveraging LangGraph and LangChain patterns for agentic orchestration.
9. THE Agent_Brain SHALL operate a dedicated Consumer_Group named "mq-guardian-agent-brain" for all Kafka consumption, enabling coordinated partition assignment and offset tracking across Agent_Brain instances.
10. THE Agent_Brain SHALL simultaneously consume from the following Event_Bus topics: "mq-telemetry-raw" for live topology telemetry, "mq-topology-snapshot" for snapshot events, and "mq-human-feedback" for approval decisions, using a single consumer group with per-topic partition assignment.
11. THE Agent_Brain SHALL produce structured events to the following Event_Bus topics: "mq-anomaly-detected" for anomalies, "mq-approval-needed" for approval requests, "mq-audit-log" for audit records, and "mq-predictive-alert" for forecasting alerts, attaching a Correlation_ID to every produced message.
12. WHEN the Agent_Brain's Kafka Consumer undergoes a consumer rebalance (partition reassignment), THE Agent_Brain SHALL commit current offsets before rebalance, restore processing state from the last committed offset after rebalance, and log the rebalance event to the "mq-audit-log" topic.
13. WHEN the Agent_Brain fails to process a consumed message after 3 retry attempts, THE Agent_Brain SHALL route the failed message to the "mq-dead-letter" Event_Bus topic with the original message payload, error description, retry count, and Correlation_ID.
14. WHEN the rate of incoming events on any consumed topic exceeds the Agent_Brain's processing capacity, THE Agent_Brain SHALL activate Backpressure handling by pausing consumption on the overloaded topic, processing the existing backlog, and resuming consumption when the backlog drops below a configurable threshold.
15. WHEN an operator requests event replay for debugging or recovery, THE Agent_Brain SHALL support resetting its consumer offset to a specified timestamp or offset position on any consumed topic and reprocessing events from that point.
16. THE Agent_Brain SHALL track consumer lag (difference between latest produced offset and current consumed offset) for each consumed topic partition and publish a lag metric to Prometheus, and WHEN consumer lag exceeds a configurable threshold, THE Agent_Brain SHALL publish a warning to the "mq-anomaly-detected" topic.

### Requirement 15: Target Topology CSV Export

**User Story:** As a hackathon judge, I want the target-state topology exported as CSV files in the same format as the input, so that I can validate the transformation independently.

#### Acceptance Criteria

1. WHEN a target topology transformation is complete, THE Platform SHALL export the target-state topology as CSV files with column structure identical to the input CSV files.
2. WHEN the Platform exports target CSV files, THE Platform SHALL include all queue managers, queues (local, remote, transmission), channels, and application-to-queue-manager mappings.
3. FOR ALL valid Topology_Snapshots, parsing the exported CSV into the Graph_Store and re-exporting SHALL produce CSV files equivalent to the original export (round-trip property).

### Requirement 16: Topology Visualization

**User Story:** As a platform operator, I want visual representations of both as-is and target topologies, so that I can understand and communicate topology changes clearly.

#### Acceptance Criteria

1. THE Platform SHALL generate topology visualizations showing queue managers as nodes and channels as edges, with application associations labeled on each queue manager node.
2. WHEN both as-is and target topologies are available, THE Platform SHALL provide a side-by-side visual comparison highlighting added, removed, and unchanged elements.
3. THE Platform SHALL export topology visualizations in DOT graph format for integration with the existing Web_UI.

### Requirement 17: Data Residency and Security

**User Story:** As a security officer, I want all MQ topology data to remain within the corporate network, so that sensitive infrastructure data is protected.

#### Acceptance Criteria

1. THE Platform SHALL process and store all CSV data, topology snapshots, and audit records exclusively within the corporate network boundary.
2. THE Platform SHALL encrypt all data at rest using AES-256 or equivalent encryption.
3. THE Platform SHALL encrypt all data in transit between components using TLS 1.2 or higher.
4. THE Platform SHALL authenticate all API requests using corporate identity provider integration (OAuth 2.0 or mTLS).
5. IF an unauthenticated or unauthorized request is received, THEN THE Platform SHALL reject the request with an appropriate HTTP error code and log the attempt in the Audit_Store.

### Requirement 18: Local Development and Deployment

**User Story:** As a developer, I want to run the entire platform locally using Docker Compose, so that I can develop and test without external dependencies.

#### Acceptance Criteria

1. THE Platform SHALL provide a Docker Compose configuration that starts all platform components including Neo4j, Kafka, the Agent_Brain, the Chatbot, Prometheus, and Grafana.
2. WHEN a developer runs "docker-compose up", THE Platform SHALL have all components healthy and communicating within 120 seconds.
3. THE Platform SHALL provide seed data and configuration files sufficient to demonstrate a complete workflow from CSV ingestion through target topology export.

### Requirement 19: Application Onboarding Automation

**User Story:** As a platform operator, I want the Agent_Brain to automatically determine the MQ objects needed when a new application is onboarded, generate the required IaC artifacts, and fold the new objects into the compliant target-state topology, so that onboarding is fast, consistent, and maintains topology compliance.

#### Acceptance Criteria

1. WHEN a new application onboarding request is received during Steady_State_Mode, THE Agent_Brain SHALL analyze the request and determine the required MQ objects including queues, queue managers, channels, producers, and consumers.
2. WHEN the Agent_Brain determines the required MQ objects for a new application, THE Agent_Brain SHALL validate that the proposed objects comply with all MQ constraints enforced by the Policy_Engine before proceeding.
3. WHEN the required MQ objects are determined and validated, THE Agent_Brain SHALL generate Terraform configuration artifacts for provisioning the new MQ infrastructure.
4. WHEN the required MQ objects are determined and validated, THE Agent_Brain SHALL generate Ansible playbook artifacts for configuring the new MQ objects on the target queue managers.
5. WHEN the Agent_Brain generates IaC artifacts for a new application, THE Agent_Brain SHALL route the proposed onboarding changes through the HiTL approval workflow before execution.
6. WHEN onboarding IaC artifacts are approved and successfully applied, THE Agent_Brain SHALL fold all new MQ objects into the target-state topology in the Graph_Store, ensuring the updated topology remains compliant and optimized.
7. WHEN the Agent_Brain folds new objects into the target-state topology, THE Agent_Brain SHALL verify that the updated topology does not introduce constraint violations, increased complexity, or Drift from the compliant baseline.
8. THE Agent_Brain SHALL publish an audit event to the "mq-audit-log" Event_Bus topic for each application onboarding action including object determination, IaC generation, approval decision, and topology update.

### Requirement 20: Continuous Drift Detection and Remediation (Deepened Kafka Consumer Management)

**User Story:** As a platform operator, I want the platform to continuously detect drift from the compliant target-state topology in real-time using robust Kafka consumer group management, and automatically propose remediation actions, so that the MQ environment remains in a compliant and optimized state at all times.

#### Acceptance Criteria

1. WHILE operating in Steady_State_Mode, THE Agent_Brain SHALL run an embedded Kafka Consumer within the "mq-guardian-agent-brain" Consumer_Group that continuously subscribes to topology change events on the "mq-telemetry-raw" and "mq-topology-snapshot" Event_Bus topics.
2. THE Agent_Brain SHALL compare the current live topology state against the established target-state topology stored in the Graph_Store at configurable intervals with a default of every 5 minutes.
3. WHEN the Agent_Brain detects a deviation between the live topology and the target-state topology, THE Agent_Brain SHALL classify the Drift as "configuration_drift" (object property changes), "structural_drift" (added or removed objects), or "policy_drift" (compliance violations introduced).
4. WHEN Drift is detected, THE Agent_Brain SHALL generate a remediation proposal containing the specific corrective actions needed to restore the topology to the compliant target state, along with a Correlation_ID linking the drift event to the remediation.
5. WHEN a remediation proposal is generated, THE Agent_Brain SHALL route the proposal through the HiTL approval workflow, publishing an approval request to the "mq-approval-needed" Event_Bus topic with the Blast_Radius, Risk_Score, and Decision_Report attached.
6. WHEN a drift remediation is approved, THE Agent_Brain SHALL generate the corresponding Terraform and Ansible artifacts and execute them through the IaC_Engine via the GitOps pipeline.
7. WHEN a drift remediation is successfully applied, THE Agent_Brain SHALL update the target-state topology in the Graph_Store to reflect the corrected state and verify that the topology is fully compliant.
8. THE Agent_Brain SHALL publish all drift detection events, remediation proposals, and remediation outcomes to the "mq-audit-log" Event_Bus topic for full traceability, with Correlation_IDs linking related events.
9. WHEN the Agent_Brain's Kafka Consumer for drift monitoring loses connectivity to the Event_Bus, THE Agent_Brain SHALL retry connection with exponential backoff (starting at 1 second, maximum 60 seconds) and publish a connectivity alert to the Monitoring_Stack.
10. WHEN the Agent_Brain's Kafka Consumer resumes after a connectivity interruption, THE Agent_Brain SHALL resume consumption from the last committed offset to avoid missing topology change events.
11. THE Agent_Brain SHALL commit Kafka consumer offsets only after successfully processing and persisting the drift analysis result, ensuring at-least-once processing semantics.

### Requirement 21: Explainable Decision Engine

**User Story:** As a platform operator, I want the Agent_Brain to provide clear, natural language explanations for every topology change proposal including the reasoning chain, alternatives considered, and policy references, so that I can understand and trust the agent's recommendations before approving them.

#### Acceptance Criteria

1. WHEN the Agent_Brain proposes a topology change or remediation, THE Agent_Brain SHALL generate a Decision_Report containing a natural language explanation of the reasoning behind the recommendation.
2. WHEN the Agent_Brain generates a Decision_Report, THE Agent_Brain SHALL include a reasoning chain showing the sequential logic steps from the detected issue to the proposed solution.
3. WHEN the Agent_Brain generates a Decision_Report, THE Agent_Brain SHALL reference the specific MQ constraints, Sentinel policies, or OPA policies that influenced the decision.
4. WHEN the Agent_Brain evaluates a topology change, THE Agent_Brain SHALL identify alternative options that were considered and include in the Decision_Report the reason each alternative was rejected.
5. WHEN the Agent_Brain generates a Decision_Report, THE Agent_Brain SHALL attach the report to the corresponding approval request published to the "mq-approval-needed" Event_Bus topic and persist the report in the Audit_Store.
6. WHEN an operator submits a "what-if" query via the Chatbot (e.g., "what if we move App X to QM Y instead?"), THE Agent_Brain SHALL evaluate the hypothetical change against the Graph_Store and Policy_Engine, and return a Decision_Report describing the projected outcome, constraint compliance, and Complexity_Metric impact without modifying the production topology.
7. THE Agent_Brain SHALL format Decision_Reports in a structured JSON schema containing fields for: summary, reasoning_chain (ordered list of steps), policy_references (list of policy IDs and descriptions), alternatives_considered (list of alternative actions with rejection reasons), risk_assessment, and recommendation.

### Requirement 22: Topology Impact Analysis

**User Story:** As a platform operator, I want the Agent_Brain to compute the blast radius, risk score, downstream dependencies, and rollback plan for every proposed topology change before it is submitted for approval, so that I can make informed decisions about the safety and scope of each change.

#### Acceptance Criteria

1. WHEN the Agent_Brain generates a topology change proposal, THE Agent_Brain SHALL compute the Blast_Radius by traversing the Graph_Store to identify all applications, queues, channels, and queue managers that would be directly or transitively affected by the proposed change.
2. WHEN the Agent_Brain computes the Blast_Radius, THE Agent_Brain SHALL estimate a Risk_Score (low, medium, high, critical) based on the number of affected objects, the criticality classification of affected applications, and historical change outcome data.
3. WHEN the Agent_Brain computes the Blast_Radius, THE Agent_Brain SHALL identify all downstream dependencies including applications that consume from affected queues, channels that route through affected queue managers, and transmission queues that depend on affected channels.
4. WHEN the Agent_Brain generates a topology change proposal, THE Agent_Brain SHALL generate a rollback plan specifying the exact reversal actions needed to restore the topology to its pre-change state.
5. WHEN the Agent_Brain generates a topology change proposal, THE Agent_Brain SHALL simulate the proposed change against the Graph_Store topology model (without modifying the production graph) and validate that the post-change topology satisfies all MQ constraints and policies.
6. THE Agent_Brain SHALL include the Blast_Radius, Risk_Score, downstream dependency list, and rollback plan in every approval request published to the "mq-approval-needed" Event_Bus topic.
7. THE Agent_Brain SHALL expose the impact analysis results via a REST API endpoint returning a JSON response for integration with the Monitoring_Stack dashboards.

### Requirement 23: Capacity Planning and Forecasting

**User Story:** As a platform operator, I want the Agent_Brain to analyze historical topology growth patterns and predict future capacity needs, so that I can proactively scale MQ infrastructure before capacity limits are reached.

#### Acceptance Criteria

1. WHEN historical Topology_Snapshot data spanning at least 30 days is available in the Graph_Store, THE Agent_Brain SHALL analyze topology growth patterns including queue manager count trends, queue count trends, channel count trends, and message volume trends.
2. WHEN the Agent_Brain analyzes growth patterns, THE Agent_Brain SHALL use Prophet ML forecasting to predict when individual queue managers will reach configurable capacity thresholds (default: 80% of maximum queue count, 80% of maximum channel count).
3. WHEN the Agent_Brain predicts that a queue manager will reach a capacity threshold within a configurable forecast horizon (default: 30 days), THE Agent_Brain SHALL publish a proactive capacity alert to the "mq-predictive-alert" Event_Bus topic containing the affected queue manager, predicted threshold breach date, current utilization, and recommended scaling action.
4. WHEN a planned application onboarding request is received, THE Agent_Brain SHALL forecast the impact of the new application on topology complexity by simulating the onboarding against the current Graph_Store model and reporting the projected Complexity_Metric change.
5. THE Agent_Brain SHALL generate a capacity report on a configurable schedule (default: weekly) showing current utilization per queue manager, projected utilization at 30/60/90 day horizons, and recommended scaling actions, and publish the report to the "mq-audit-log" Event_Bus topic.
6. THE Agent_Brain SHALL expose capacity planning data via a REST API endpoint returning a JSON response for integration with the "Predictive Maintenance" Grafana dashboard.

### Requirement 24: Topology Optimization Recommendations

**User Story:** As a platform operator, I want the Agent_Brain to actively suggest topology improvements beyond compliance maintenance, including queue manager consolidation, channel elimination, clustering optimizations, and placement strategies, so that the MQ environment is continuously optimized for reduced complexity and operational efficiency.

#### Acceptance Criteria

1. WHEN the Agent_Brain analyzes the target-state topology in the Graph_Store, THE Agent_Brain SHALL identify queue managers that could be consolidated without violating MQ constraints, and report the consolidation candidates with the projected Complexity_Metric reduction.
2. WHEN the Agent_Brain analyzes the target-state topology in the Graph_Store, THE Agent_Brain SHALL identify channels that could be eliminated by rerouting message flows through fewer hops, and report the elimination candidates with the projected channel count reduction.
3. WHEN the Agent_Brain analyzes the target-state topology in the Graph_Store, THE Agent_Brain SHALL suggest neighborhood or region-based clustering optimizations that group related queue managers to minimize cross-region channel traffic.
4. WHEN a new application onboarding request is received, THE Agent_Brain SHALL recommend an optimal queue manager placement strategy for the new application based on existing topology patterns, neighborhood affinity, and Complexity_Metric minimization.
5. WHEN the Agent_Brain generates an optimization recommendation, THE Agent_Brain SHALL calculate and report the Complexity_Metric reduction achievable by each individual recommendation and by all recommendations combined.
6. WHEN the Agent_Brain generates optimization recommendations, THE Agent_Brain SHALL route each recommendation through the HiTL approval workflow as a non-urgent proposal, clearly distinguishing optimization proposals from compliance-driven remediations.
7. THE Agent_Brain SHALL generate optimization recommendations on a configurable schedule (default: weekly) and publish the recommendations to the "mq-audit-log" Event_Bus topic.

### Requirement 25: Change Simulation and Sandbox

**User Story:** As a platform operator, I want to simulate proposed topology changes against a copy of the topology graph in a sandbox environment, compose multiple changes, and review before/after metrics before committing to production, so that I can safely experiment with topology modifications without risk to the live environment.

#### Acceptance Criteria

1. WHEN an operator requests Sandbox_Mode via the Chatbot or API, THE Platform SHALL create an in-memory copy of the current target-state topology from the Graph_Store for simulation purposes.
2. WHEN the Platform creates a sandbox topology copy, THE Platform SHALL isolate the sandbox from the production Graph_Store so that sandbox operations do not modify the production target-state topology.
3. WHEN an operator proposes a change in Sandbox_Mode, THE Platform SHALL apply the change to the sandbox topology copy and compute the before and after Complexity_Metric values for comparison.
4. WHEN an operator composes multiple changes in Sandbox_Mode, THE Platform SHALL apply the changes sequentially to the sandbox topology copy and show the cumulative Complexity_Metric impact of all composed changes.
5. WHEN changes are applied in Sandbox_Mode, THE Platform SHALL validate constraint compliance of the simulated post-change topology against the Policy_Engine and report any violations.
6. WHEN an operator is satisfied with a simulated change set in Sandbox_Mode, THE Platform SHALL allow the operator to export the simulated target state as a formal change proposal that enters the standard HiTL approval workflow.
7. WHEN an operator exits Sandbox_Mode without exporting, THE Platform SHALL discard the sandbox topology copy without affecting the production Graph_Store.
8. THE Platform SHALL support concurrent sandbox sessions for different operators without interference between sessions.

### Requirement 26: Multi-LOB Topology Governance

**User Story:** As an enterprise platform administrator, I want the platform to support topology partitioning by Line of Business with LOB-specific policies, delegated approval workflows, LOB-level metrics, and cross-LOB dependency tracking, so that multiple Lines of Business can operate within a shared MQ environment with appropriate governance boundaries.

#### Acceptance Criteria

1. THE Platform SHALL support partitioning the target-state topology in the Graph_Store by LOB, assigning each queue manager, queue, channel, and application to exactly one LOB based on metadata from the ingested CSV data or operator assignment.
2. THE Policy_Engine SHALL support LOB-specific policy rules in addition to global MQ constraint policies, and WHEN a proposed change affects objects within a specific LOB, THE Policy_Engine SHALL evaluate both global policies and the LOB-specific policies applicable to that LOB.
3. THE Platform SHALL compute and expose LOB-level Complexity_Metrics showing the channel count, queue count, queue manager count, and average routing hops within each LOB partition, accessible via the REST API and the Monitoring_Stack dashboards.
4. WHEN a proposed topology change affects objects within a single LOB, THE Platform SHALL route the approval request to the designated LOB owner for that LOB through the HiTL approval workflow.
5. WHEN a proposed topology change affects objects across multiple LOBs, THE Platform SHALL route the approval request to all affected LOB owners and require approval from each affected LOB owner before the Agent_Brain proceeds with execution.
6. THE Agent_Brain SHALL track cross-LOB dependencies in the Graph_Store by identifying channels and message flows that traverse LOB boundaries, and WHEN a proposed change affects a cross-LOB dependency, THE Agent_Brain SHALL flag the cross-LOB impact in the Blast_Radius analysis and Decision_Report.
7. THE Platform SHALL provide a REST API endpoint to query the topology filtered by LOB, returning only the queue managers, queues, channels, and applications belonging to the specified LOB.
