"""MQ topology validators — constraint enforcement for target-state topologies.

Enforces the core MQ routing invariants:
  1) 1-QM-per-App (deterministic mapping via SHA1)
  2) writes_to: App → QM_for(App) only
  3) reads_from: QM_for(App) → App only
  4) transit_via_channel_pair: QM → QM only

Zero external dependencies — uses only Python stdlib.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict


def normalize_app(s: str) -> str:
    """Canonicalize app IDs: collapse whitespace, trim, uppercase."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip().upper()


def qm_for(app: str) -> str:
    """Deterministic 1-to-1 mapping from app → QM.

    Uses stable SHA1-derived 4-char suffix for readability.
    """
    if not app:
        return ""
    a = normalize_app(app)
    h = hashlib.sha1(a.encode("utf-8")).hexdigest()[:4].upper()
    return f"QM_{h}"


def build_edges_target(
    intents: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Build canonical target-state edges from logical flows.

    Each (producer, consumer) intent produces three edges:
        App_P → QM_P  (writes_to)
        QM_P  → QM_C  (transit_via_channel_pair)
        QM_C  → App_C (reads_from)

    Deduplicates edges.
    """
    seen: set[tuple[str, str, str]] = set()
    edges: list[tuple[str, str, str]] = []

    for P, C in intents:
        Pn, Cn = normalize_app(P), normalize_app(C)
        qm_p, qm_c = qm_for(Pn), qm_for(Cn)

        triplet = [
            (Pn, qm_p, "writes_to"),
            (qm_p, qm_c, "transit_via_channel_pair"),
            (qm_c, Cn, "reads_from"),
        ]
        for s, d, r in triplet:
            key = (s, d, r)
            if key not in seen:
                seen.add(key)
                edges.append((s, d, r))

    return edges


def validate_edges_target(
    edges: list[tuple[str, str, str]],
) -> tuple[bool, list[str], dict]:
    """Validate target-state edges against MQ routing invariants.

    Returns (ok, errors, details) where:
      - ok: True if all invariants hold
      - errors: list of human-readable violation descriptions
      - details: dict with 'multi' (apps attached to >1 QM)
    """
    errs: list[str] = []

    # 1) 1-QM-per-App
    app_to_qms: defaultdict[str, set[str]] = defaultdict(set)
    for s, d, rel in edges:
        s_is_qm = s.startswith("QM_")
        d_is_qm = d.startswith("QM_")
        if not s_is_qm and d_is_qm:
            app_to_qms[s].add(d)
        if s_is_qm and not d_is_qm:
            app_to_qms[d].add(s)

    multi = [a for a, q in app_to_qms.items() if len(q) > 1]
    if multi:
        errs.append(f"Apps attached to >1 QM: {len(multi)}")

    # 2/3/4) Check hop semantics
    for s, d, rel in edges:
        s_is_qm = s.startswith("QM_")
        d_is_qm = d.startswith("QM_")

        if rel == "writes_to":
            if s_is_qm or not d_is_qm:
                errs.append(f"writes_to must be App->QM: {s}->{d}")
            elif d != qm_for(s):
                errs.append(f"writes_to wrong QM: expected {qm_for(s)} got {d} for App {s}")
        elif rel == "reads_from":
            if not s_is_qm or d_is_qm:
                errs.append(f"reads_from must be QM->App: {s}->{d}")
            elif s != qm_for(d):
                errs.append(f"reads_from wrong QM: expected {qm_for(d)} got {s} for App {d}")
        elif rel == "transit_via_channel_pair":
            if not (s_is_qm and d_is_qm):
                errs.append(f"transit must be QM->QM: {s}->{d}")
        else:
            errs.append(f"Unknown relation: {rel}")

    return (len(errs) == 0, errs, {"multi": multi})
