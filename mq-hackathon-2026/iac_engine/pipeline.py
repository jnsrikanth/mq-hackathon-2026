"""IaC Pipeline — orchestrates Terraform and Ansible for MQ infrastructure.

The pipeline flow:
  1. Generate Terraform modules from target topology
  2. Run `terraform plan` (dry-run) → present to operator
  3. On approval → create GitHub PR with TF configs
  4. On PR merge → `terraform apply` via TFE
  5. On TF success → generate Ansible playbooks
  6. Run `ansible-playbook --check` (dry-run) → present to operator
  7. On approval → `ansible-playbook` (apply)
  8. On success → update target state in Graph Store

For the hackathon demo, steps 3-4 and 6-7 are simulated locally.
The artifacts (TF files, Ansible playbooks) are real and valid.

Requirements: 8.4, 8.5, 8.9, 8.10, 8.11, 8b.5, 8b.7, 8b.9
"""

from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from iac_engine.terraform_generator import TerraformModuleGenerator
from iac_engine.ansible_generator import AnsiblePlaybookGenerator

logger = logging.getLogger(__name__)


class IaCPipeline:
    """Orchestrates the full IaC pipeline: TF plan → PR → apply → Ansible."""

    def __init__(self, artifacts_dir: str = "artifacts") -> None:
        self.artifacts_dir = artifacts_dir
        self.tf_gen = TerraformModuleGenerator()
        self.ansible_gen = AnsiblePlaybookGenerator()

    def execute_full_pipeline(
        self,
        target_topology: dict,
        environment: str = "prod",
    ) -> dict:
        """Execute the complete IaC pipeline.

        Returns a pipeline result dict with all generated artifacts,
        plan summaries, and execution status.
        """
        pipeline_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        result = {
            "pipeline_id": pipeline_id,
            "environment": environment,
            "started_at": started_at.isoformat(),
            "stages": [],
            "artifacts": {},
            "status": "running",
        }

        # Stage 1: Generate Terraform
        tf_dir = os.path.join(self.artifacts_dir, "terraform", environment)
        try:
            tf_files = self.tf_gen.generate_all(target_topology, tf_dir, environment)
            result["stages"].append({
                "stage": "terraform_generate",
                "status": "success",
                "files": {k: os.path.basename(v) for k, v in tf_files.items()},
                "message": f"Generated {len(tf_files)} Terraform files",
            })
            result["artifacts"]["terraform"] = tf_files
        except Exception as e:
            result["stages"].append({
                "stage": "terraform_generate",
                "status": "failed",
                "error": str(e),
            })
            result["status"] = "failed"
            return result

        # Stage 2: Terraform Plan (simulated)
        plan_path = tf_files.get("plan_summary")
        if plan_path and os.path.exists(plan_path):
            with open(plan_path) as f:
                plan = json.load(f)
            result["stages"].append({
                "stage": "terraform_plan",
                "status": "success",
                "plan": plan,
                "message": f"Plan: {plan['actions']['create']} to create, {plan['actions']['update']} to update, {plan['actions']['destroy']} to destroy",
            })
        else:
            result["stages"].append({
                "stage": "terraform_plan",
                "status": "success",
                "message": "Plan generated (dry-run)",
            })

        # Stage 3: Terraform Apply (simulated for demo)
        result["stages"].append({
            "stage": "terraform_apply",
            "status": "success",
            "message": "Terraform apply completed (simulated — in production, this triggers TFE via GitHub PR merge)",
            "tfe_workspace": f"mq-guardian-{environment}",
        })

        # Stage 4: Generate Ansible
        ansible_dir = os.path.join(self.artifacts_dir, "ansible", environment)
        try:
            ansible_files = self.ansible_gen.generate_all(target_topology, ansible_dir)
            result["stages"].append({
                "stage": "ansible_generate",
                "status": "success",
                "files": {k: os.path.basename(v) for k, v in ansible_files.items()},
                "message": f"Generated {len(ansible_files)} Ansible artifacts",
            })
            result["artifacts"]["ansible"] = ansible_files
        except Exception as e:
            result["stages"].append({
                "stage": "ansible_generate",
                "status": "failed",
                "error": str(e),
            })
            result["status"] = "failed"
            return result

        # Stage 5: Ansible Dry-Run (simulated)
        result["stages"].append({
            "stage": "ansible_check",
            "status": "success",
            "message": "Ansible check mode passed (simulated — in production, runs ansible-playbook --check)",
        })

        # Stage 6: Ansible Apply (simulated)
        result["stages"].append({
            "stage": "ansible_apply",
            "status": "success",
            "message": "Ansible playbook executed (simulated — in production, configures MQ objects on provisioned QMs)",
        })

        result["status"] = "completed"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_ms"] = int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        )

        logger.info("IaC pipeline %s completed: %d stages", pipeline_id, len(result["stages"]))
        return result

    def generate_terraform_only(
        self, target_topology: dict, environment: str = "prod",
    ) -> dict:
        """Generate Terraform files without applying."""
        tf_dir = os.path.join(self.artifacts_dir, "terraform", environment)
        return self.tf_gen.generate_all(target_topology, tf_dir, environment)

    def generate_ansible_only(
        self, target_topology: dict,
    ) -> dict:
        """Generate Ansible playbooks without executing."""
        ansible_dir = os.path.join(self.artifacts_dir, "ansible")
        return self.ansible_gen.generate_all(target_topology, ansible_dir)
