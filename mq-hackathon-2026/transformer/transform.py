"""Target-state topology transformer.

Reads the enterprise AS-IS CSV, enforces MQ routing invariants, and produces:
  - Target edge graph (edges_target_v2.csv)
  - Channel pairs (channels.csv)
  - Target MQ topology CSV (same column structure as input)
  - Metrics and DOT graph for the Web UI

Routing policy enforced by construction:
  - 1-QM-per-App (deterministic SHA1 mapping)
  - Producer: App → QM(App) (writes_to)
  - Transit: QM(App) → QM(App) (transit_via_channel_pair)
  - Consumer: QM(App) → App (reads_from)

Zero external dependencies — uses only Python stdlib.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Optional

from transformer.validators import (
    build_edges_target,
    normalize_app,
    qm_for,
    validate_edges_target,
)


# ---------------------------------------------------------------------------
# Input reader
# ---------------------------------------------------------------------------

def unique_flows(flows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse duplicate (Producer, Consumer) intents using normalized names."""
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for P, C in flows:
        Pn, Cn = normalize_app(P), normalize_app(C)
        key = (Pn, Cn)
        if key not in seen:
            seen.add(key)
            uniq.append((Pn, Cn))
    return uniq


def read_as_is_csv(path: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Read the AS-IS CSV and yield logical flows (producer, consumer).

    Robust to column naming — probes common variants.

    Returns:
        flows: list of (producer, consumer) tuples
        header: original column names (for schema-identical target CSV)
    """
    flows: list[tuple[str, str]] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []

        cols = {c.lower(): c for c in header}
        producer_candidates = [
            "producername", "producer", "src_app", "sourceapp",
            "source", "from", "from_app", "fromapp",
        ]
        consumer_candidates = [
            "consumername", "consumer", "dst_app", "targetapp",
            "target", "to", "to_app", "toapp",
        ]

        prod_col = next((cols[c] for c in producer_candidates if c in cols), None)
        cons_col = next((cols[c] for c in consumer_candidates if c in cols), None)

        if not prod_col or not cons_col:
            if "ProducerName" in header and "ConsumerName" in header:
                prod_col = "ProducerName"
                cons_col = "ConsumerName"
            else:
                raise ValueError(
                    "Cannot detect Producer/Consumer columns. "
                    "Ensure the CSV has ProducerName and ConsumerName."
                )

        for row in reader:
            P = row.get(prod_col, "")
            C = row.get(cons_col, "")
            if P and C:
                flows.append((P, C))

    return flows, header


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------

def write_edges_csv(
    edges: list[tuple[str, str, str]], out_path: str,
) -> None:
    """Write edges to CSV with columns: src, dst, relation."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["src", "dst", "relation"])
        for s, d, r in edges:
            w.writerow([s, d, r])


def write_channels_csv(
    edges: list[tuple[str, str, str]], out_path: str,
) -> None:
    """From QM→QM transits produce deterministic channel pairs.

    Channel naming: CH.<fromQM>.to.<toQM>, CH.<toQM>.from.<fromQM>
    """
    pairs: set[tuple[str, str]] = set()
    for s, d, r in edges:
        if r == "transit_via_channel_pair" and s.startswith("QM_") and d.startswith("QM_"):
            pairs.add((s, d))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_qm", "to_qm", "sender_channel", "receiver_channel"])
        for a, b in sorted(pairs):
            w.writerow([a, b, f"CH.{a}.to.{b}", f"CH.{b}.from.{a}"])


def write_target_csv(
    flows: list[tuple[str, str]],
    out_path: str,
    original_header: list[str] | None = None,
    pretty_map: dict[str, str] | None = None,
) -> None:
    """Emit Target.MQ.Topology.csv using the same column order as input.

    Each logical flow produces two rows:
      1. Producer-side remoteQ (QREMOTE) on QM_P
      2. Consumer-side local queue (QLOCAL) on QM_C
    """
    default_header = [
        "queue_manager_name", "Discrete Queue Name",
        "remote_q_mgr_name", "remote_q_name", "xmit_q_name",
        "q_type", "ProducerName", "ConsumerName",
    ]
    header = list(original_header) if original_header else default_header

    def rowdict() -> dict[str, str]:
        return {k: "" for k in header}

    def has(col: str) -> bool:
        return col in header

    flows_sorted = sorted(flows, key=lambda t: (t[0], t[1]))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()

        for Pn, Cn in flows_sorted:
            P_disp = pretty_map.get(Pn, Pn) if pretty_map else Pn
            C_disp = pretty_map.get(Cn, Cn) if pretty_map else Cn
            QM_P, QM_C = qm_for(Pn), qm_for(Cn)

            rq_name = f"RQ.{C_disp}"
            lq_name = f"LQ.{C_disp}"
            xq = f"XQ.{QM_C}"

            # Producer-side remoteQ (QREMOTE) on QM_P
            r1 = rowdict()
            if has("queue_manager_name"): r1["queue_manager_name"] = QM_P
            if has("Discrete Queue Name"): r1["Discrete Queue Name"] = rq_name
            if has("remote_q_mgr_name"): r1["remote_q_mgr_name"] = QM_C
            if has("remote_q_name"): r1["remote_q_name"] = lq_name
            if has("xmit_q_name"): r1["xmit_q_name"] = xq
            if has("q_type"): r1["q_type"] = "QREMOTE"
            if has("ProducerName"): r1["ProducerName"] = P_disp
            if has("ConsumerName"): r1["ConsumerName"] = C_disp
            w.writerow(r1)

            # Consumer-side local queue (QLOCAL) on QM_C
            r2 = rowdict()
            if has("queue_manager_name"): r2["queue_manager_name"] = QM_C
            if has("Discrete Queue Name"): r2["Discrete Queue Name"] = lq_name
            if has("q_type"): r2["q_type"] = "QLOCAL"
            if has("ProducerName"): r2["ProducerName"] = P_disp
            if has("ConsumerName"): r2["ConsumerName"] = C_disp
            w.writerow(r2)


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_target_metrics(
    edges: list[tuple[str, str, str]],
) -> dict:
    """Compute topology metrics from target edges."""
    nodes: set[str] = set()
    degree: dict[str, int] = {}

    for s, d, _r in edges:
        nodes.add(s)
        nodes.add(d)
        degree[s] = degree.get(s, 0) + 1
        degree[d] = degree.get(d, 0) + 1

    unique_qms = sum(1 for n in nodes if n.startswith("QM_"))
    qm_degs = sorted(
        ((n, degree.get(n, 0)) for n in nodes if n.startswith("QM_")),
        key=lambda x: x[1],
        reverse=True,
    )
    top5 = [[n, d] for n, d in qm_degs[:5]]
    max_hub = qm_degs[0][1] if qm_degs else 0

    # TCS formula matches AS-IS analyzer for apples-to-apples comparison
    brittle = 0  # by construction, 1-QM-per-App
    tcs = len(edges) + 5 * brittle + 2 * max_hub

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "unique_queue_managers": unique_qms,
        "brittle_apps": brittle,
        "tcs_score": tcs,
        "top5_hub_qm": top5,
    }


def build_target_dot(edges: list[tuple[str, str, str]]) -> str:
    """Build a DOT graph string from target edges."""
    lines = ["digraph MQ {", "  rankdir=LR;"]
    for s, d, r in edges:
        lines.append(f'  "{s}" -> "{d}" [label="{r}"];')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_transform(
    as_is_csv_path: str,
    out_dir: str,
) -> dict:
    """Transform AS-IS topology to target-state.

    Returns:
      - on success: {ok, artifacts, target_report, target_dot}
      - on invariant failure: {error, errors}
    """
    os.makedirs(out_dir, exist_ok=True)

    flows_raw, original_header = read_as_is_csv(as_is_csv_path)

    # Build pretty-name map (first seen original strings)
    pretty: dict[str, str] = {}
    for P, C in flows_raw:
        Pn, Cn = normalize_app(P), normalize_app(C)
        pretty.setdefault(Pn, P)
        pretty.setdefault(Cn, C)

    # Collapse duplicate intents
    flows = unique_flows(flows_raw)

    # Build and validate target edges
    edges = build_edges_target(flows)
    ok, errs, _details = validate_edges_target(edges)

    if not ok:
        return {"error": "Target invariants failed", "errors": errs}

    # Write artifacts
    edges_path = os.path.join(out_dir, "edges_target_v2.csv")
    ch_path = os.path.join(out_dir, "channels.csv")
    tgt_csv = os.path.join(out_dir, "Target.MQ.Topology.csv")

    write_edges_csv(edges, edges_path)
    write_channels_csv(edges, ch_path)
    write_target_csv(flows, tgt_csv, original_header=original_header, pretty_map=pretty)

    metrics = compute_target_metrics(edges)
    target_dot = build_target_dot(edges)

    return {
        "ok": True,
        "artifacts": {
            "edges_target": edges_path,
            "channels": ch_path,
            "target_csv": tgt_csv,
        },
        "target_report": {"topology_metrics": metrics},
        "target_dot": target_dot,
    }
