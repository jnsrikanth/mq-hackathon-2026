"""Topology analyzer — computes metrics and DOT graphs from MQ CSV data.

Reads the enterprise CSV format and produces:
  - Topology metrics (nodes, edges, QMs, hub analysis, brittle apps, TCS score)
  - Edge list for graph construction
  - DOT graph string for visualization

Zero external dependencies — uses only Python stdlib.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict


def analyze_topology(path: str) -> tuple[dict, dict]:
    """Analyze an MQ topology CSV and compute metrics.

    Returns:
        metrics: dict with keys: rows, total_nodes, total_edges,
                 unique_queue_managers, top5_hub_qm, brittle_apps, tcs_score
        extras: dict with 'edges' (list of (src, dst, rel) tuples),
                'qm_in' (Counter), 'qm_out' (Counter)
    """
    qms: set[str] = set()
    apps: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    app_to_qms: defaultdict[str, set[str]] = defaultdict(set)
    qm_in: Counter[str] = Counter()
    qm_out: Counter[str] = Counter()

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            producer = (row.get("ProducerName") or "").strip()
            consumer = (row.get("ConsumerName") or "").strip()
            qm = (row.get("queue_manager_name") or "").strip()

            if not qm:
                continue

            qms.add(qm)

            if producer:
                apps.add(producer)
                e = (producer, qm, "writes_to")
                if e not in edges:
                    edges.add(e)
                    qm_in[qm] += 1
                    app_to_qms[producer].add(qm)

            if consumer:
                apps.add(consumer)
                e = (qm, consumer, "reads_from")
                if e not in edges:
                    edges.add(e)
                    qm_out[qm] += 1
                    app_to_qms[consumer].add(qm)

    qm_degree = {k: qm_in[k] + qm_out[k] for k in qms}
    top5 = sorted(qm_degree.items(), key=lambda x: x[1], reverse=True)[:5]
    brittle = sum(1 for _a, s in app_to_qms.items() if len(s) > 1)
    tcs = len(edges) + brittle * 5 + (top5[0][1] * 2 if top5 else 0)

    metrics = {
        "rows": len(edges),
        "total_nodes": len(qms) + len(apps),
        "total_edges": len(edges),
        "unique_queue_managers": len(qms),
        "top5_hub_qm": top5,
        "brittle_apps": brittle,
        "tcs_score": tcs,
    }

    return metrics, {
        "edges": list(edges),
        "qm_in": dict(qm_in),
        "qm_out": dict(qm_out),
    }


def edges_to_dot(edges: list[tuple[str, str, str]]) -> str:
    """Build a Graphviz DOT string from edge tuples.

    QM_ prefixed nodes are rendered as boxes; others as ellipses.
    """
    nodes_qm: set[str] = set()
    nodes_app: set[str] = set()

    for s, d, _rel in edges:
        (nodes_qm if s.startswith("QM_") else nodes_app).add(s)
        (nodes_qm if d.startswith("QM_") else nodes_app).add(d)

    lines: list[str] = [
        "digraph MQ {",
        "  rankdir=LR;",
        '  node [fontsize=9];',
    ]

    if nodes_qm:
        labels = " ".join(f'"{n}"' for n in sorted(nodes_qm))
        lines.append(
            f'  {{ node [shape=box, style=filled, fillcolor="#F0F6FF"]; {labels}; }}'
        )

    if nodes_app:
        labels = " ".join(f'"{n}"' for n in sorted(nodes_app))
        lines.append(
            f'  {{ node [shape=ellipse, style=filled, fillcolor="#FCF5FF"]; {labels}; }}'
        )

    for s, d, rel in sorted(edges):
        color = "#1F6FEB" if rel == "writes_to" else "#7D4AEA"
        lines.append(f'  "{s}" -> "{d}" [label="{rel}", color="{color}"];')

    lines.append("}")
    return "\n".join(lines)
