"""Pure-Python in-memory Graph Store for the MQ Guardian Platform.

Provides a Neo4j-compatible interface using Python dicts and adjacency lists.
All topology data is stored in-memory with the same API contract as a real
Neo4j-backed implementation, enabling a future swap to Neo4j when enterprise
infrastructure is available.

Merged capabilities from the lightweight Graph service:
- BFS path finding between any two nodes (paths_between)
- Degree-based hub analysis (top_hubs)
- MQ constraint validation (check_constraints)
- CSV edge file loading for Web UI integration (load_edges_csv)

Requirements: 2.1, 2.2, 2.3, 2.4, 12.2, 12.5, 25.1, 25.2, 26.1, 26.6, 26.7
"""

from __future__ import annotations

import copy
import csv
import logging
import os
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

from models.domain import (
    Application,
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)

logger = logging.getLogger(__name__)


class Neo4jGraphStore:
    """Pure-Python in-memory graph store with Neo4j-compatible interface.

    Nodes are stored as dicts keyed by ``(label, id)``.  Edges are stored
    in an adjacency list keyed by ``(from_label, from_id)`` mapping to
    lists of ``(rel_type, to_label, to_id, properties)``.

    Drop-in replacement: when Neo4j is available, swap this class for a
    real driver-backed implementation with the same method signatures.
    """

    def __init__(self, uri: str = "", user: str = "", password: str = "") -> None:
        # Connection params stored for future Neo4j migration
        self._uri = uri
        self._user = user
        self._password = password

        # Node storage: {(label, id): {properties}}
        self._nodes: dict[tuple[str, str], dict] = {}

        # Edge storage: {(from_label, from_id): [(rel_type, to_label, to_id, props)]}
        self._edges: defaultdict[tuple[str, str], list[tuple[str, str, str, dict]]] = (
            defaultdict(list)
        )

        # Version tracking
        self._versions: dict[str, dict] = {}  # tag -> {tag, timestamp, snapshot_id, is_current}
        self._version_members: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)

        # Audit log (append-only)
        self._audit_events: list[dict] = []

        logger.info("In-memory GraphStore initialised (uri=%s)", uri or "local")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """No-op for in-memory store. Provided for API compatibility."""
        logger.info("In-memory GraphStore closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upsert_node(self, label: str, node_id: str, props: dict) -> None:
        key = (label, node_id)
        if key in self._nodes:
            self._nodes[key].update(props)
        else:
            self._nodes[key] = {"id": node_id, **props}

    def _add_edge(
        self, from_label: str, from_id: str,
        rel_type: str, to_label: str, to_id: str,
        props: dict | None = None,
    ) -> None:
        key = (from_label, from_id)
        # Avoid duplicate edges
        edge = (rel_type, to_label, to_id, props or {})
        existing = self._edges[key]
        for e in existing:
            if e[0] == rel_type and e[1] == to_label and e[2] == to_id:
                return  # already exists
        existing.append(edge)

    def _get_nodes_by_label(self, label: str) -> list[dict]:
        return [props for (lbl, _), props in self._nodes.items() if lbl == label]

    def _get_node(self, label: str, node_id: str) -> dict | None:
        return self._nodes.get((label, node_id))

    def _get_neighbours(
        self, label: str, node_id: str, rel_type: str | None = None,
    ) -> list[tuple[str, str, str, dict]]:
        """Get outgoing edges from a node, optionally filtered by rel_type."""
        edges = self._edges.get((label, node_id), [])
        if rel_type:
            return [e for e in edges if e[0] == rel_type]
        return list(edges)

    def _get_reverse_neighbours(
        self, label: str, node_id: str, rel_type: str | None = None,
    ) -> list[tuple[str, str, str]]:
        """Get incoming edges to a node."""
        results = []
        for (fl, fid), edges in self._edges.items():
            for rt, tl, tid, _ in edges:
                if tl == label and tid == node_id:
                    if rel_type is None or rt == rel_type:
                        results.append((fl, fid, rt))
        return results

    # ------------------------------------------------------------------
    # Snapshot loading & versioning  (Req 2.1, 2.2, 2.3)
    # ------------------------------------------------------------------

    def load_snapshot(
        self, snapshot: TopologySnapshotEvent, version_tag: str
    ) -> None:
        """Create/update nodes and edges for all MQ objects.

        Preserves all original CSV attributes as node properties.
        Retains previous snapshot as a versioned historical record.
        """
        # 1. Mark previous current version as historical
        for tag, meta in self._versions.items():
            if meta.get("is_current"):
                meta["is_current"] = False

        # 2. Create version record
        self._versions[version_tag] = {
            "tag": version_tag,
            "timestamp": snapshot.timestamp.isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "is_current": True,
        }

        members: set[tuple[str, str]] = set()

        # 3. Upsert QueueManagers
        for qm in snapshot.queue_managers:
            self._upsert_node("QueueManager", qm.id, {
                "name": qm.name, "hostname": qm.hostname, "port": qm.port,
                "neighborhood": qm.neighborhood, "region": qm.region,
                "lob": qm.lob, "max_queues": qm.max_queues,
                "max_channels": qm.max_channels, "metadata": qm.metadata,
            })
            members.add(("QueueManager", qm.id))
            if qm.lob:
                self._upsert_node("LOB", qm.lob, {"name": qm.lob})
                self._add_edge("QueueManager", qm.id, "BELONGS_TO", "LOB", qm.lob)

        # 4. Upsert Queues
        for q in snapshot.queues:
            self._upsert_node("Queue", q.id, {
                "name": q.name, "queue_type": q.queue_type.value,
                "owning_qm_id": q.owning_qm_id, "target_qm_id": q.target_qm_id,
                "xmitq_id": q.xmitq_id, "lob": q.lob, "metadata": q.metadata,
            })
            members.add(("Queue", q.id))
            self._add_edge("Queue", q.id, "HOSTED_ON", "QueueManager", q.owning_qm_id)
            if q.queue_type == QueueType.REMOTE and q.xmitq_id:
                self._add_edge("Queue", q.id, "ROUTES_VIA", "Queue", q.xmitq_id)

        # 5. Upsert Channels
        for ch in snapshot.channels:
            self._upsert_node("Channel", ch.id, {
                "name": ch.name, "direction": ch.direction.value,
                "from_qm_id": ch.from_qm_id, "to_qm_id": ch.to_qm_id,
                "lob": ch.lob, "metadata": ch.metadata,
            })
            members.add(("Channel", ch.id))
            self._add_edge("Channel", ch.id, "CONNECTS_FROM", "QueueManager", ch.from_qm_id)
            self._add_edge("Channel", ch.id, "CONNECTS_TO", "QueueManager", ch.to_qm_id)

        # 6. Upsert Applications
        for app in snapshot.applications:
            self._upsert_node("Application", app.id, {
                "name": app.name, "connected_qm_id": app.connected_qm_id,
                "role": app.role, "criticality": app.criticality,
                "lob": app.lob, "metadata": app.metadata,
            })
            members.add(("Application", app.id))
            self._add_edge("Application", app.id, "CONNECTS_TO", "QueueManager", app.connected_qm_id)
            for qid in app.produces_to:
                self._add_edge("Application", app.id, "PRODUCES_TO", "Queue", qid)
            for qid in app.consumes_from:
                self._add_edge("Application", app.id, "CONSUMES_FROM", "Queue", qid)

        self._version_members[version_tag] = members
        logger.info("Loaded snapshot %s as version %s", snapshot.snapshot_id, version_tag)

    # ------------------------------------------------------------------
    # Topology retrieval  (Req 2.4, 26.7)
    # ------------------------------------------------------------------

    def get_current_topology(
        self, lob: Optional[str] = None
    ) -> TopologySnapshotEvent:
        """Retrieve current target-state topology, optionally filtered by LOB."""
        current_tag = None
        for tag, meta in self._versions.items():
            if meta.get("is_current"):
                current_tag = tag
                break
        if current_tag is None:
            raise ValueError("No current topology version found in the graph store.")
        meta = self._versions[current_tag]
        return self._build_snapshot(current_tag, meta["timestamp"], meta["snapshot_id"], lob=lob)

    def get_historical_snapshot(self, version_tag: str) -> TopologySnapshotEvent:
        """Retrieve a specific historical topology version."""
        if version_tag not in self._versions:
            raise ValueError(f"Topology version '{version_tag}' not found.")
        meta = self._versions[version_tag]
        return self._build_snapshot(version_tag, meta["timestamp"], meta["snapshot_id"])

    def _build_snapshot(
        self, version_tag: str, timestamp_str: str, snapshot_id: str,
        *, lob: Optional[str] = None,
    ) -> TopologySnapshotEvent:
        members = self._version_members.get(version_tag, set())

        qms = []
        qm_ids: set[str] = set()
        for label, nid in members:
            if label == "QueueManager":
                props = self._nodes[(label, nid)]
                if lob and props.get("lob") != lob:
                    continue
                qms.append(self._props_to_qm(props))
                qm_ids.add(nid)

        queues = []
        for label, nid in members:
            if label == "Queue":
                props = self._nodes[(label, nid)]
                if lob and props.get("owning_qm_id") not in qm_ids:
                    continue
                queues.append(self._props_to_queue(props))

        channels = []
        for label, nid in members:
            if label == "Channel":
                props = self._nodes[(label, nid)]
                if lob and props.get("from_qm_id") not in qm_ids and props.get("to_qm_id") not in qm_ids:
                    continue
                channels.append(self._props_to_channel(props))

        apps = []
        for label, nid in members:
            if label == "Application":
                props = self._nodes[(label, nid)]
                if lob and props.get("connected_qm_id") not in qm_ids:
                    continue
                apps.append(self._props_to_app(props))

        return TopologySnapshotEvent(
            snapshot_id=snapshot_id,
            correlation_id="",
            timestamp=datetime.fromisoformat(timestamp_str),
            mode="bootstrap",
            queue_managers=qms,
            queues=queues,
            channels=channels,
            applications=apps,
        )

    # ------------------------------------------------------------------
    # Record → domain model helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _props_to_qm(p: dict) -> QueueManager:
        return QueueManager(
            id=p["id"], name=p.get("name", ""), hostname=p.get("hostname", ""),
            port=p.get("port", 0), neighborhood=p.get("neighborhood"),
            region=p.get("region"), lob=p.get("lob"),
            max_queues=p.get("max_queues", 5000),
            max_channels=p.get("max_channels", 1000), metadata={},
        )

    @staticmethod
    def _props_to_queue(p: dict) -> Queue:
        return Queue(
            id=p["id"], name=p.get("name", ""),
            queue_type=QueueType(p.get("queue_type", "local")),
            owning_qm_id=p.get("owning_qm_id", ""),
            target_qm_id=p.get("target_qm_id"),
            xmitq_id=p.get("xmitq_id"), lob=p.get("lob"), metadata={},
        )

    @staticmethod
    def _props_to_channel(p: dict) -> Channel:
        return Channel(
            id=p["id"], name=p.get("name", ""),
            direction=ChannelDirection(p.get("direction", "sender")),
            from_qm_id=p.get("from_qm_id", ""),
            to_qm_id=p.get("to_qm_id", ""), lob=p.get("lob"), metadata={},
        )

    @staticmethod
    def _props_to_app(p: dict) -> Application:
        return Application(
            id=p["id"], name=p.get("name", ""),
            connected_qm_id=p.get("connected_qm_id", ""),
            role=p.get("role", "consumer"),
            criticality=p.get("criticality"), lob=p.get("lob"), metadata={},
        )

    # ------------------------------------------------------------------
    # Blast radius & dependency queries  (Req 22.1, 22.3, 26.6)
    # ------------------------------------------------------------------

    def query_blast_radius(self, change_proposal: dict) -> dict:
        """Traverse graph for direct and transitive affected objects."""
        object_ids: list[str] = change_proposal.get("object_ids", [])
        if not object_ids:
            return {"direct": [], "transitive": []}

        seed_keys: set[tuple[str, str]] = set()
        for (label, nid) in self._nodes:
            if nid in object_ids:
                seed_keys.add((label, nid))

        # Direct: 1-hop neighbours
        direct: list[dict] = []
        direct_ids: set[str] = set(object_ids)
        for label, nid in seed_keys:
            # Outgoing
            for rt, tl, tid, _ in self._edges.get((label, nid), []):
                if tl not in ("TopologyVersion", "LOB", "AuditEvent") and tid not in direct_ids:
                    direct.append({"id": tid, "label": tl, "name": self._nodes.get((tl, tid), {}).get("name", "")})
                    direct_ids.add(tid)
            # Incoming
            for fl, fid, rt in self._get_reverse_neighbours(label, nid):
                if fl not in ("TopologyVersion", "LOB", "AuditEvent") and fid not in direct_ids:
                    direct.append({"id": fid, "label": fl, "name": self._nodes.get((fl, fid), {}).get("name", "")})
                    direct_ids.add(fid)

        # Transitive: 2-hop from direct set (excluding already found)
        transitive: list[dict] = []
        trans_ids: set[str] = set(direct_ids)
        for d in direct:
            d_label, d_id = d["label"], d["id"]
            for rt, tl, tid, _ in self._edges.get((d_label, d_id), []):
                if tl not in ("TopologyVersion", "LOB", "AuditEvent") and tid not in trans_ids:
                    transitive.append({"id": tid, "label": tl, "name": self._nodes.get((tl, tid), {}).get("name", "")})
                    trans_ids.add(tid)
            for fl, fid, rt in self._get_reverse_neighbours(d_label, d_id):
                if fl not in ("TopologyVersion", "LOB", "AuditEvent") and fid not in trans_ids:
                    transitive.append({"id": fid, "label": fl, "name": self._nodes.get((fl, fid), {}).get("name", "")})
                    trans_ids.add(fid)

        return {"direct": direct, "transitive": transitive}

    def query_downstream_dependencies(self, object_ids: list[str]) -> dict:
        """Find downstream consumers, channels, and xmitqs."""
        if not object_ids:
            return {"consumers": [], "channels": [], "xmitqs": []}

        consumers: list[dict] = []
        channels: list[dict] = []
        xmitqs: list[dict] = []
        seen_c: set[str] = set()
        seen_ch: set[str] = set()
        seen_xq: set[str] = set()

        for oid in object_ids:
            # Find channels connected to this object
            for (label, nid), edges in self._edges.items():
                if label == "Channel":
                    props = self._nodes.get(("Channel", nid), {})
                    if props.get("from_qm_id") == oid or props.get("to_qm_id") == oid:
                        if nid not in seen_ch:
                            channels.append({"id": nid, "name": props.get("name", "")})
                            seen_ch.add(nid)

            # Find consumers: apps that CONSUMES_FROM queues hosted on this QM
            for (label, nid), edges in self._edges.items():
                if label == "Application":
                    for rt, tl, tid, _ in edges:
                        if rt == "CONSUMES_FROM":
                            q_props = self._nodes.get(("Queue", tid), {})
                            if q_props.get("owning_qm_id") == oid and nid not in seen_c:
                                consumers.append({"id": nid, "name": self._nodes.get(("Application", nid), {}).get("name", "")})
                                seen_c.add(nid)

            # Find xmitqs hosted on this QM
            for (label, nid) in self._nodes:
                if label == "Queue":
                    props = self._nodes[(label, nid)]
                    if props.get("queue_type") == "transmission" and props.get("owning_qm_id") == oid:
                        if nid not in seen_xq:
                            xmitqs.append({"id": nid, "name": props.get("name", "")})
                            seen_xq.add(nid)

        return {"consumers": consumers, "channels": channels, "xmitqs": xmitqs}

    def query_cross_lob_dependencies(self, lob: str) -> list[dict]:
        """Identify channels and flows crossing LOB boundaries."""
        results: list[dict] = []
        seen: set[str] = set()
        for (label, nid) in self._nodes:
            if label == "Channel" and nid not in seen:
                props = self._nodes[(label, nid)]
                from_qm = self._nodes.get(("QueueManager", props.get("from_qm_id", "")), {})
                to_qm = self._nodes.get(("QueueManager", props.get("to_qm_id", "")), {})
                from_lob = from_qm.get("lob")
                to_lob = to_qm.get("lob")
                if from_lob and to_lob and from_lob != to_lob:
                    if from_lob == lob or to_lob == lob:
                        results.append({
                            "channel_id": nid,
                            "channel_name": props.get("name", ""),
                            "from_lob": from_lob,
                            "to_lob": to_lob,
                        })
                        seen.add(nid)
        return results

    # ------------------------------------------------------------------
    # LOB assignment  (Req 26.1)
    # ------------------------------------------------------------------

    def assign_lob(self, object_id: str, lob: str) -> None:
        """Assign an MQ object to a Line of Business."""
        for label in ("QueueManager", "Queue", "Channel", "Application"):
            key = (label, object_id)
            if key in self._nodes:
                self._nodes[key]["lob"] = lob
                if label == "QueueManager":
                    self._upsert_node("LOB", lob, {"name": lob})
                    self._add_edge("QueueManager", object_id, "BELONGS_TO", "LOB", lob)
                logger.info("Assigned %s %s to LOB %s", label, object_id, lob)
                return
        logger.warning("Object %s not found for LOB assignment", object_id)

    # ------------------------------------------------------------------
    # Sandbox support  (Req 25.1, 25.2)
    # ------------------------------------------------------------------

    def create_sandbox_copy(self, session_id: str) -> dict:
        """Create a deep copy of the current topology for sandbox simulation.

        Returns a dict handle containing the copied graph data that can be
        used independently without affecting the production graph.
        """
        return {
            "session_id": session_id,
            "nodes": copy.deepcopy(dict(self._nodes)),
            "edges": copy.deepcopy(dict(self._edges)),
            "versions": copy.deepcopy(self._versions),
            "version_members": copy.deepcopy(dict(self._version_members)),
        }

    # ------------------------------------------------------------------
    # Audit logging  (Req 12.2, 12.5)
    # ------------------------------------------------------------------

    def persist_audit_event(self, event: dict) -> None:
        """Store an immutable audit record. Append-only — never modified."""
        record = {
            "id": event.get("event_id", str(uuid.uuid4())),
            "correlation_id": event.get("correlation_id", ""),
            "timestamp": event.get("timestamp", datetime.now().isoformat()),
            "actor": event.get("actor", "system"),
            "action_type": event.get("action_type", ""),
            "affected_objects": event.get("affected_objects", []),
            "outcome": event.get("outcome", ""),
            "details": event.get("details", {}),
        }
        self._audit_events.append(record)
        logger.info("Persisted audit event %s", record["id"])

    def query_audit_log(
        self,
        time_range: Optional[tuple[str, str]] = None,
        action_type: Optional[str] = None,
        actor: Optional[str] = None,
        affected_object: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> list[dict]:
        """Query audit records with optional filters."""
        results = []
        for evt in self._audit_events:
            if time_range:
                if evt["timestamp"] < time_range[0] or evt["timestamp"] > time_range[1]:
                    continue
            if action_type and evt["action_type"] != action_type:
                continue
            if actor and evt["actor"] != actor:
                continue
            if affected_object and affected_object not in evt.get("affected_objects", []):
                continue
            if correlation_id and evt["correlation_id"] != correlation_id:
                continue
            results.append(evt)
        return sorted(results, key=lambda e: e["timestamp"], reverse=True)

    # ------------------------------------------------------------------
    # Graph traversal — BFS path finding (merged from Graph service)
    # ------------------------------------------------------------------

    def paths_between(
        self, src_id: str, dst_id: str, *, limit: int = 5,
    ) -> list[list[dict]]:
        """Find up to *limit* shortest paths between two nodes using BFS.

        Returns a list of paths, where each path is a list of hop dicts:
        ``[{"from": id, "to": id, "relation": rel_type, "from_label": ..., "to_label": ...}, ...]``

        Works across all node labels — finds the source and destination
        by ID regardless of their type.
        """
        # Resolve source and destination keys
        src_key = self._resolve_node_key(src_id)
        dst_key = self._resolve_node_key(dst_id)
        if src_key is None or dst_key is None:
            return []

        # BFS using (label, id) tuples as state
        results: list[list[tuple[str, str]]] = []
        queue: deque[list[tuple[str, str]]] = deque([[src_key]])
        visited: set[tuple[str, str]] = {src_key}
        found_len: int | None = None

        while queue and (found_len is None or len(queue[0]) <= found_len):
            path = queue.popleft()
            current = path[-1]

            if current == dst_key:
                results.append(path)
                found_len = len(path)
                if len(results) >= limit:
                    break
                continue

            # Expand outgoing edges
            for rt, tl, tid, _ in self._edges.get(current, []):
                next_key = (tl, tid)
                if next_key not in visited:
                    visited.add(next_key)
                    queue.append(path + [next_key])

            # Expand incoming edges (undirected traversal)
            for fl, fid, rt in self._get_reverse_neighbours(current[0], current[1]):
                next_key = (fl, fid)
                if next_key not in visited:
                    visited.add(next_key)
                    queue.append(path + [next_key])

        # Convert raw paths to detailed hop dicts
        output: list[list[dict]] = []
        for raw_path in results:
            hops: list[dict] = []
            for i in range(len(raw_path) - 1):
                fl, fid = raw_path[i]
                tl, tid = raw_path[i + 1]
                rel = self._find_edge_relation(fl, fid, tl, tid)
                hops.append({
                    "from": fid, "to": tid,
                    "from_label": fl, "to_label": tl,
                    "relation": rel,
                })
            output.append(hops)
        return output

    def _resolve_node_key(self, node_id: str) -> tuple[str, str] | None:
        """Find the (label, id) key for a node by its ID."""
        for (label, nid) in self._nodes:
            if nid == node_id:
                return (label, nid)
        return None

    def _find_edge_relation(
        self, from_label: str, from_id: str, to_label: str, to_id: str,
    ) -> str:
        """Find the relationship type between two specific nodes."""
        for rt, tl, tid, _ in self._edges.get((from_label, from_id), []):
            if tl == to_label and tid == to_id:
                return rt
        # Check reverse direction
        for rt, tl, tid, _ in self._edges.get((to_label, to_id), []):
            if tl == from_label and tid == from_id:
                return f"<-{rt}"
        return ""

    # ------------------------------------------------------------------
    # Degree analysis — hub detection (merged from Graph service)
    # ------------------------------------------------------------------

    def compute_degree(self) -> dict[str, int]:
        """Compute degree (in + out) for every node in the graph.

        Returns a dict mapping node_id to total degree count.
        Excludes internal nodes (TopologyVersion, LOB, AuditEvent).
        """
        degree: dict[str, int] = {}
        excluded = {"TopologyVersion", "LOB", "AuditEvent"}

        for (label, nid) in self._nodes:
            if label in excluded:
                continue
            out_count = sum(
                1 for rt, tl, tid, _ in self._edges.get((label, nid), [])
                if tl not in excluded
            )
            in_count = sum(
                1 for fl, fid, rt in self._get_reverse_neighbours(label, nid)
                if fl not in excluded
            )
            degree[nid] = out_count + in_count
        return degree

    def top_hubs(self, k: int = 10) -> list[tuple[str, str, int]]:
        """Return the top-k nodes by degree (most connected).

        Returns list of ``(node_id, label, degree)`` tuples sorted by:
        1. QueueManagers first (they're the natural hubs)
        2. Highest degree
        3. Alphabetical name as tiebreaker
        """
        degree = self.compute_degree()
        excluded = {"TopologyVersion", "LOB", "AuditEvent"}

        items: list[tuple[str, str, int]] = []
        for (label, nid), props in self._nodes.items():
            if label in excluded:
                continue
            items.append((nid, label, degree.get(nid, 0)))

        def sort_key(item: tuple[str, str, int]) -> tuple[int, int, str]:
            nid, label, deg = item
            is_qm = 0 if label == "QueueManager" else 1
            return (is_qm, -deg, nid)

        return sorted(items, key=sort_key)[:k]

    # ------------------------------------------------------------------
    # MQ constraint checks (merged from Graph service)
    # ------------------------------------------------------------------

    def check_constraints(self) -> dict:
        """Validate MQ topology constraints against the current graph.

        Checks:
        - Each application connects to exactly one QueueManager
        - Producers write to remote queues on their own QM
        - Consumers read from local queues on their own QM

        Returns a dict with ``ok`` (bool), ``errors`` (list[str]),
        and ``sample_violations`` (list of violating object IDs).
        """
        errors: list[str] = []
        violations: list[str] = []

        # 1. One QM per app
        app_to_qms: defaultdict[str, set[str]] = defaultdict(set)
        for (label, nid) in self._nodes:
            if label == "Application":
                props = self._nodes[(label, nid)]
                qm_id = props.get("connected_qm_id")
                if qm_id:
                    app_to_qms[nid].add(qm_id)

        multi_qm_apps = [aid for aid, qms in app_to_qms.items() if len(qms) > 1]
        if multi_qm_apps:
            errors.append(f"Apps connected to >1 QM: {len(multi_qm_apps)}")
            violations.extend(multi_qm_apps[:5])

        # 2. Producers should write to remote queues on their own QM
        for (label, nid) in self._nodes:
            if label != "Application":
                continue
            props = self._nodes[(label, nid)]
            if props.get("role") not in ("producer", "both"):
                continue
            app_qm = props.get("connected_qm_id")
            for rt, tl, tid, _ in self._edges.get((label, nid), []):
                if rt == "PRODUCES_TO" and tl == "Queue":
                    q_props = self._nodes.get(("Queue", tid), {})
                    if q_props.get("owning_qm_id") != app_qm:
                        errors.append(
                            f"Producer {nid} writes to queue {tid} on different QM "
                            f"(app QM={app_qm}, queue QM={q_props.get('owning_qm_id')})"
                        )
                        if nid not in violations:
                            violations.append(nid)

        # 3. Consumers should read from local queues on their own QM
        for (label, nid) in self._nodes:
            if label != "Application":
                continue
            props = self._nodes[(label, nid)]
            if props.get("role") not in ("consumer", "both"):
                continue
            app_qm = props.get("connected_qm_id")
            for rt, tl, tid, _ in self._edges.get((label, nid), []):
                if rt == "CONSUMES_FROM" and tl == "Queue":
                    q_props = self._nodes.get(("Queue", tid), {})
                    if q_props.get("owning_qm_id") != app_qm:
                        errors.append(
                            f"Consumer {nid} reads from queue {tid} on different QM "
                            f"(app QM={app_qm}, queue QM={q_props.get('owning_qm_id')})"
                        )
                        if nid not in violations:
                            violations.append(nid)

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "sample_violations": violations[:10],
        }

    # ------------------------------------------------------------------
    # CSV edge loading — Web UI integration (merged from Graph service)
    # ------------------------------------------------------------------

    def load_edges_csv(self, filepath: str) -> int:
        """Load edges from a CSV file with columns: src, dst, relation.

        This is the bridge to the Web UI's graph service. The CSV format
        matches the edge export format used by the topology transformer.

        Nodes are auto-created from edge endpoints. Node labels are
        inferred from naming conventions (QM_ prefix → QueueManager, etc.)
        or default to "Node".

        Returns the number of edges loaded.
        """
        if not os.path.exists(filepath):
            logger.warning("Edge CSV not found: %s", filepath)
            return 0

        count = 0
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                src = (row.get("src") or "").strip()
                dst = (row.get("dst") or "").strip()
                rel = (row.get("relation") or "").strip()
                if not src or not dst:
                    continue

                src_label = self._infer_label(src)
                dst_label = self._infer_label(dst)

                self._upsert_node(src_label, src, {"name": src})
                self._upsert_node(dst_label, dst, {"name": dst})
                self._add_edge(src_label, src, rel or "CONNECTED", dst_label, dst)
                count += 1

        logger.info("Loaded %d edges from %s", count, filepath)
        return count

    def export_edges_csv(self, filepath: str) -> int:
        """Export all edges to a CSV file with columns: src, dst, relation.

        Returns the number of edges written.
        """
        count = 0
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["src", "dst", "relation"])
            writer.writeheader()
            for (from_label, from_id), edges in self._edges.items():
                for rel_type, to_label, to_id, _ in edges:
                    writer.writerow({"src": from_id, "dst": to_id, "relation": rel_type})
                    count += 1
        logger.info("Exported %d edges to %s", count, filepath)
        return count

    @staticmethod
    def _infer_label(node_id: str) -> str:
        """Infer node label from naming convention."""
        nid = node_id.upper()
        if nid.startswith("QM_") or nid.startswith("QM."):
            return "QueueManager"
        if nid.startswith("QL_") or nid.startswith("QL."):
            return "Queue"
        if nid.startswith("QR_") or nid.startswith("QR."):
            return "Queue"
        if nid.startswith("XQ_") or nid.startswith("XQ."):
            return "Queue"
        if nid.startswith("CH_") or nid.startswith("CH."):
            return "Channel"
        if nid.startswith("APP_") or nid.startswith("APP."):
            return "Application"
        return "Node"

    # ------------------------------------------------------------------
    # DOT export for visualization
    # ------------------------------------------------------------------

    def export_dot(self, *, title: str = "MQ Topology") -> str:
        """Export the graph as a DOT string for visualization.

        Compatible with the Web UI's DOT-to-SVG renderer.
        """
        excluded = {"TopologyVersion", "LOB", "AuditEvent"}
        lines = [f'digraph "{title}" {{', "  rankdir=LR;"]

        # Node declarations with shape by label
        shape_map = {
            "QueueManager": "box", "Queue": "ellipse",
            "Channel": "diamond", "Application": "hexagon", "Node": "circle",
        }
        for (label, nid), props in self._nodes.items():
            if label in excluded:
                continue
            shape = shape_map.get(label, "circle")
            name = props.get("name", nid)
            lines.append(f'  "{nid}" [label="{name}" shape={shape}];')

        # Edges
        for (from_label, from_id), edges in self._edges.items():
            if from_label in excluded:
                continue
            for rel_type, to_label, to_id, _ in edges:
                if to_label in excluded:
                    continue
                lines.append(f'  "{from_id}" -> "{to_id}" [label="{rel_type}"];')

        lines.append("}")
        return "\n".join(lines)
