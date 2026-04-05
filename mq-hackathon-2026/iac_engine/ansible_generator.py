"""Ansible Playbook Generator for MQ object configuration.

After Terraform provisions the infrastructure (QMs, networking),
Ansible configures the MQ objects on those QMs:
  - Queue creation (local, remote, transmission)
  - Channel pair configuration
  - Security settings (TLS, auth)
  - Service restart handlers

Output is YAML playbooks ready for `ansible-playbook --check` (dry-run)
then `ansible-playbook` (apply).

Requirements: 8b.1, 8b.2, 8b.3, 8b.4, 8b.5, 8b.6, 8b.7, 8b.8, 8b.10
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import yaml


class AnsiblePlaybookGenerator:
    """Generates Ansible playbooks for MQ object configuration."""

    def generate_all(
        self, target_topology: dict, out_dir: str,
    ) -> dict:
        """Generate all Ansible artifacts.

        Returns a dict of generated file paths.
        """
        os.makedirs(out_dir, exist_ok=True)
        files = {}

        # Main playbook
        playbook = self._generate_playbook(target_topology)
        path = os.path.join(out_dir, "configure_mq.yml")
        self._write_yaml(path, playbook)
        files["playbook"] = path

        # Inventory
        inventory = self._generate_inventory(target_topology)
        path = os.path.join(out_dir, "inventory.yml")
        self._write_yaml(path, inventory)
        files["inventory"] = path

        # Roles
        roles_dir = os.path.join(out_dir, "roles")
        os.makedirs(roles_dir, exist_ok=True)
        for role_name, role_tasks in self._generate_roles(target_topology).items():
            role_dir = os.path.join(roles_dir, role_name, "tasks")
            os.makedirs(role_dir, exist_ok=True)
            path = os.path.join(role_dir, "main.yml")
            self._write_yaml(path, role_tasks)
            files[f"role_{role_name}"] = path

        # Handlers
        handlers = self._generate_handlers()
        path = os.path.join(out_dir, "handlers.yml")
        self._write_yaml(path, handlers)
        files["handlers"] = path

        return files

    def _generate_playbook(self, topo: dict) -> list[dict]:
        """Generate the main MQ configuration playbook."""
        return [
            {
                "name": "MQ Guardian — Configure Target Topology",
                "hosts": "queue_managers",
                "become": True,
                "vars": {
                    "mq_install_path": "/opt/mqm",
                    "tls_enabled": True,
                    "managed_by": "mq-guardian-platform",
                },
                "roles": [
                    "qm_setup",
                    "queue_creation",
                    "channel_pair_creation",
                    "security_config",
                ],
                "handlers": [
                    {"include": "handlers.yml"},
                ],
            }
        ]

    def _generate_inventory(self, topo: dict) -> dict:
        """Generate dynamic inventory grouped by QM."""
        edges = topo.get("edges", [])
        qms = set()
        for e in edges:
            src, dst = e.get("src", ""), e.get("dst", "")
            if src.startswith("QM_"): qms.add(src)
            if dst.startswith("QM_"): qms.add(dst)

        hosts = {}
        for qm in sorted(qms):
            hosts[qm] = {
                "ansible_host": f"{qm.lower()}.mq.internal",
                "mq_qm_name": qm,
                "mq_port": 1414,
            }

        return {
            "all": {
                "children": {
                    "queue_managers": {
                        "hosts": hosts,
                    },
                },
            },
        }

    def _generate_roles(self, topo: dict) -> dict[str, list[dict]]:
        """Generate reusable Ansible roles."""
        edges = topo.get("edges", [])
        channels = topo.get("channels", [])

        # QM Setup role
        qm_setup = [
            {
                "name": "Ensure queue manager is running",
                "command": "dspmq -m {{ mq_qm_name }}",
                "register": "qm_status",
                "changed_when": False,
                "failed_when": False,
            },
            {
                "name": "Start queue manager if not running",
                "command": "strmqm {{ mq_qm_name }}",
                "when": "'Running' not in qm_status.stdout",
            },
        ]

        # Queue Creation role
        queue_tasks = [
            {
                "name": "Create local queues",
                "command": 'echo "DEFINE QLOCAL({{ item.name }}) REPLACE" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ local_queues | default([]) }}",
                "notify": "refresh_mq_config",
            },
            {
                "name": "Create remote queues",
                "command": 'echo "DEFINE QREMOTE({{ item.name }}) RNAME({{ item.remote_name }}) RQMNAME({{ item.remote_qm }}) XMITQ({{ item.xmitq }}) REPLACE" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ remote_queues | default([]) }}",
                "notify": "refresh_mq_config",
            },
            {
                "name": "Create transmission queues",
                "command": 'echo "DEFINE QLOCAL({{ item.name }}) USAGE(XMITQ) REPLACE" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ transmission_queues | default([]) }}",
                "notify": "refresh_mq_config",
            },
        ]

        # Channel Pair Creation role
        channel_tasks = [
            {
                "name": "Create sender channels",
                "command": 'echo "DEFINE CHANNEL({{ item.sender }}) CHLTYPE(SDR) CONNAME({{ item.remote_host }}({{ item.remote_port }})) XMITQ({{ item.xmitq }}) REPLACE" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ sender_channels | default([]) }}",
                "notify": "refresh_mq_config",
            },
            {
                "name": "Create receiver channels",
                "command": 'echo "DEFINE CHANNEL({{ item.receiver }}) CHLTYPE(RCVR) REPLACE" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ receiver_channels | default([]) }}",
                "notify": "refresh_mq_config",
            },
        ]

        # Security Config role
        security_tasks = [
            {
                "name": "Enable TLS on channels",
                "command": 'echo "ALTER CHANNEL({{ item }}) SSLCIPH(TLS_RSA_WITH_AES_256_CBC_SHA256)" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ tls_channels | default([]) }}",
                "when": "tls_enabled | default(true)",
                "notify": "restart_mq_listener",
            },
            {
                "name": "Set channel authentication",
                "command": 'echo "SET CHLAUTH({{ item }}) TYPE(BLOCKUSER) USERLIST(nobody) ACTION(REPLACE)" | runmqsc {{ mq_qm_name }}',
                "loop": "{{ secure_channels | default([]) }}",
            },
        ]

        return {
            "qm_setup": qm_setup,
            "queue_creation": queue_tasks,
            "channel_pair_creation": channel_tasks,
            "security_config": security_tasks,
        }

    def _generate_handlers(self) -> list[dict]:
        return [
            {
                "name": "refresh_mq_config",
                "command": "echo 'REFRESH QMGR' | runmqsc {{ mq_qm_name }}",
            },
            {
                "name": "restart_mq_listener",
                "command": "endmqlsr -m {{ mq_qm_name }} && runmqlsr -m {{ mq_qm_name }} -t TCP -p {{ mq_port }}",
            },
        ]

    @staticmethod
    def _write_yaml(path: str, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
