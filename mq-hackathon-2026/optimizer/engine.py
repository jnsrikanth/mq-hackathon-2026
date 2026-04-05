"""Topology Optimizer for the MQ Guardian Platform.

Suggests topology improvements beyond compliance maintenance:
- Queue manager consolidation candidates
- Channel elimination by rerouting
- Neighborhood/region-based clustering optimizations
- Optimal QM placement for new applications
- Combined complexity reduction calculations

All methods are pure Python and operate against the in-memory
Neo4jGraphStore.

Requirements: 24.1, 24.2, 24.3, 24.4, 24.5
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from graph_store.neo4j_client import Neo4jGraphStore
from models.domain import (
    Channel,
    ChannelDirection,
    Queue,
    QueueManager,
    QueueType,
    TopologySnapshotEvent,
)

logger = logging.getLogger(__name__)


class TopologyOptimizer:
    """Suggests topology improvements beyond compliance maintenance."""

    # ------------------------------------------------------------------
    # QM consolidation  (Req 24.1)
    # ------------------------------------------------------------------

    def identify_consolidation_candidates(
        self, graph_store: Neo4jGraphStore
    ) -> list[dict]:
        """Find QMs that can be consolidated without violating constraints.

        Two QMs are consolidation candidates when:
        1. They are in the same neighborhood (or both have no neighborhood).
        2. The combined queue and channel counts fit within the target QM's
           capacity limits.
        3. No application constraint is violated (each app still connects
           to exactly one QM after merge).

        Returns a list of candidate dicts, each containing:
        - ``source_qm_id``, ``source_qm_name``
        - ``target_qm_id``, ``target_qm_name``
        - ``combined_queues``, ``combined_channels``
        - ``projected_channel_reduction`` — channels between the pair that
          would be eliminated
        - ``projected_complexity_reduction`` — estimated composite score delta
        """
        topology = graph_store.get_current_topology()

        qm_map = {qm.id: qm for qm in topology.queue_managers}
        queues_per_qm: dict[str, int] = defaultdict(int)
        channels_per_qm: dict[str, int] = defaultdict(int)

        for q in topology.queues:
            queues_per_qm[q.owning_qm_id] += 1

        for ch in topology.channels:
            channels_per_qm[ch.from_qm_id] += 1
            channels_per_qm[ch.to_qm_id] += 1

        # Build a set of channels between each QM pair
        channels_between: dict[tuple[str, str], int] = defaultdict(int)
        for ch in topology.channels:
            pair = tuple(sorted([ch.from_qm_id, ch.to_qm_id]))
            channels_between[pair] += 1  # type: ignore[index]

        # Apps per QM
        apps_per_qm: dict[str, list[str]] = defaultdict(list)
        for app in topology.applications:
            apps_per_qm[app.connected_qm_id].append(app.id)

        candidates: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()

        qm_ids = list(qm_map.keys())
        for i, src_id in enumerate(qm_ids):
            src = qm_map[src_id]
            for tgt_id in qm_ids[i + 1:]:
                tgt = qm_map[tgt_id]

                # Same neighborhood check
                if (src.neighborhood or "") != (tgt.neighborhood or ""):
                    continue

                pair = tuple(sorted([src_id, tgt_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)  # type: ignore[arg-type]

                # Capacity check — merge src into tgt
                combined_queues = queues_per_qm[src_id] + queues_per_qm[tgt_id]
                # Channels between the pair would be eliminated
                pair_channels = channels_between.get(pair, 0)  # type: ignore[arg-type]
                # External channels from src would be redirected to tgt
                combined_channels = (
                    channels_per_qm[src_id]
                    + channels_per_qm[tgt_id]
                    - 2 * pair_channels  # remove both directions
                )

                if combined_queues > tgt.max_queues:
                    continue
                if combined_channels > tgt.max_channels:
                    continue

                # Projected complexity reduction
                total_channels = len(topology.channels)
                reduction_pct = (
                    (pair_channels / total_channels * 100)
                    if total_channels > 0
                    else 0.0
                )

                candidates.append({
                    "source_qm_id": src_id,
                    "source_qm_name": src.name,
                    "target_qm_id": tgt_id,
                    "target_qm_name": tgt.name,
                    "combined_queues": combined_queues,
                    "combined_channels": combined_channels,
                    "projected_channel_reduction": pair_channels,
                    "projected_complexity_reduction": round(reduction_pct, 2),
                })

        # Sort by highest channel reduction first
        candidates.sort(
            key=lambda c: c["projected_channel_reduction"], reverse=True
        )
        return candidates

    # ------------------------------------------------------------------
    # Channel elimination  (Req 24.2)
    # ------------------------------------------------------------------

    def identify_channel_elimination(
        self, graph_store: Neo4jGraphStore
    ) -> list[dict]:
        """Find channels eliminable by rerouting through fewer hops.

        A channel is a candidate for elimination when both endpoint QMs
        share a common intermediate QM that already has channels to both
        endpoints — meaning the message can be rerouted through the
        intermediate without adding new channels.

        Returns a list of dicts, each containing:
        - ``channel_id``, ``channel_name``
        - ``from_qm_id``, ``to_qm_id``
        - ``reroute_via_qm_id`` — the intermediate QM
        - ``current_hops``, ``new_hops``
        - ``projected_channel_reduction``
        """
        topology = graph_store.get_current_topology()

        # Build adjacency: QM -> set of directly connected QMs via channels
        adjacency: dict[str, set[str]] = defaultdict(set)
        channel_lookup: dict[tuple[str, str], list[Channel]] = defaultdict(list)

        for ch in topology.channels:
            adjacency[ch.from_qm_id].add(ch.to_qm_id)
            adjacency[ch.to_qm_id].add(ch.from_qm_id)
            pair = tuple(sorted([ch.from_qm_id, ch.to_qm_id]))
            channel_lookup[pair].append(ch)  # type: ignore[index]

        candidates: list[dict] = []
        seen_channels: set[str] = set()

        for ch in topology.channels:
            if ch.id in seen_channels:
                continue

            from_qm = ch.from_qm_id
            to_qm = ch.to_qm_id

            # Find common neighbours that connect to both endpoints
            from_neighbours = adjacency.get(from_qm, set())
            to_neighbours = adjacency.get(to_qm, set())
            common = from_neighbours & to_neighbours - {from_qm, to_qm}

            for via_qm in common:
                # This channel can be rerouted: from_qm -> via_qm -> to_qm
                # using existing channels (no new channels needed)
                candidates.append({
                    "channel_id": ch.id,
                    "channel_name": ch.name,
                    "from_qm_id": from_qm,
                    "to_qm_id": to_qm,
                    "reroute_via_qm_id": via_qm,
                    "current_hops": 1,
                    "new_hops": 2,
                    "projected_channel_reduction": 1,
                })
                seen_channels.add(ch.id)
                break  # one reroute option per channel is enough

        return candidates

    # ------------------------------------------------------------------
    # Clustering optimizations  (Req 24.3)
    # ------------------------------------------------------------------

    def suggest_clustering_optimizations(
        self, graph_store: Neo4jGraphStore
    ) -> list[dict]:
        """Suggest neighborhood/region-based clustering to minimize
        cross-region channel traffic.

        Identifies QMs that have more channels to QMs in other
        neighborhoods/regions than to QMs in their own, and suggests
        reassignment.

        Returns a list of suggestion dicts, each containing:
        - ``qm_id``, ``qm_name``
        - ``current_neighborhood``, ``current_region``
        - ``suggested_neighborhood``, ``suggested_region``
        - ``cross_region_channels`` — current count
        - ``same_region_channels`` — current count
        - ``reason``
        """
        topology = graph_store.get_current_topology()
        qm_map = {qm.id: qm for qm in topology.queue_managers}

        suggestions: list[dict] = []

        for qm in topology.queue_managers:
            # Count channels by peer neighbourhood
            neighbourhood_counts: dict[str, int] = defaultdict(int)
            total_channels = 0

            for ch in topology.channels:
                peer_id = None
                if ch.from_qm_id == qm.id:
                    peer_id = ch.to_qm_id
                elif ch.to_qm_id == qm.id:
                    peer_id = ch.from_qm_id

                if peer_id is None or peer_id not in qm_map:
                    continue

                peer = qm_map[peer_id]
                peer_nb = peer.neighborhood or "unassigned"
                neighbourhood_counts[peer_nb] += 1
                total_channels += 1

            if total_channels == 0:
                continue

            current_nb = qm.neighborhood or "unassigned"
            same_count = neighbourhood_counts.get(current_nb, 0)
            cross_count = total_channels - same_count

            # Find the neighbourhood with the most connections
            best_nb = max(neighbourhood_counts, key=neighbourhood_counts.get)  # type: ignore[arg-type]
            best_count = neighbourhood_counts[best_nb]

            # Suggest reassignment if majority of channels go elsewhere
            if best_nb != current_nb and best_count > same_count:
                suggestions.append({
                    "qm_id": qm.id,
                    "qm_name": qm.name,
                    "current_neighborhood": current_nb,
                    "current_region": qm.region or "unassigned",
                    "suggested_neighborhood": best_nb,
                    "suggested_region": qm.region or "unassigned",
                    "cross_region_channels": cross_count,
                    "same_region_channels": same_count,
                    "reason": (
                        f"QM {qm.name} has {best_count} channels to "
                        f"neighborhood '{best_nb}' but only {same_count} "
                        f"to its current neighborhood '{current_nb}'."
                    ),
                })

        return suggestions

    # ------------------------------------------------------------------
    # Placement recommendation  (Req 24.4)
    # ------------------------------------------------------------------

    def recommend_placement(
        self,
        onboarding_request: dict,
        graph_store: Neo4jGraphStore,
    ) -> dict:
        """Recommend optimal QM placement for a new application.

        Evaluates each QM based on:
        1. Neighbourhood affinity — prefer QMs in the requested neighbourhood.
        2. Capacity headroom — prefer QMs with more spare capacity.
        3. Connectivity — prefer QMs that already connect to the app's
           communication partners (minimises new channels).

        ``onboarding_request`` should contain:
        - ``app_name``: name of the new application
        - ``neighborhood`` (optional): preferred neighbourhood
        - ``communicates_with`` (optional): list of app IDs the new app
          will exchange messages with
        - ``role``: "producer" | "consumer" | "both"

        Returns a dict with:
        - ``recommended_qm_id``, ``recommended_qm_name``
        - ``score_breakdown``: per-criterion scores
        - ``alternatives``: ranked list of other QMs
        - ``reason``
        """
        topology = graph_store.get_current_topology()

        if not topology.queue_managers:
            return {
                "recommended_qm_id": None,
                "recommended_qm_name": None,
                "score_breakdown": {},
                "alternatives": [],
                "reason": "No queue managers available in the topology.",
            }

        preferred_nb = onboarding_request.get("neighborhood", "")
        communicates_with = set(onboarding_request.get("communicates_with", []))

        # Build lookup: app_id -> connected_qm_id
        app_qm_map: dict[str, str] = {}
        for app in topology.applications:
            app_qm_map[app.id] = app.connected_qm_id

        # QMs that communication partners are on
        partner_qms: set[str] = set()
        for partner_id in communicates_with:
            if partner_id in app_qm_map:
                partner_qms.add(app_qm_map[partner_id])

        # Build adjacency for connectivity scoring
        adjacency: dict[str, set[str]] = defaultdict(set)
        for ch in topology.channels:
            adjacency[ch.from_qm_id].add(ch.to_qm_id)
            adjacency[ch.to_qm_id].add(ch.from_qm_id)

        # Count current load per QM
        queues_per_qm: dict[str, int] = defaultdict(int)
        channels_per_qm: dict[str, int] = defaultdict(int)
        for q in topology.queues:
            queues_per_qm[q.owning_qm_id] += 1
        for ch in topology.channels:
            channels_per_qm[ch.from_qm_id] += 1
            channels_per_qm[ch.to_qm_id] += 1

        scored: list[dict] = []

        for qm in topology.queue_managers:
            # 1. Neighbourhood affinity (0 or 1)
            nb_score = 1.0 if preferred_nb and (qm.neighborhood or "") == preferred_nb else 0.0

            # 2. Capacity headroom (0.0 – 1.0)
            queue_headroom = (
                (qm.max_queues - queues_per_qm[qm.id]) / qm.max_queues
                if qm.max_queues > 0
                else 0.0
            )
            channel_headroom = (
                (qm.max_channels - channels_per_qm[qm.id]) / qm.max_channels
                if qm.max_channels > 0
                else 0.0
            )
            capacity_score = min(queue_headroom, channel_headroom)
            capacity_score = max(0.0, capacity_score)

            # 3. Connectivity — how many partner QMs are already reachable
            #    A QM can reach itself (partner on same QM) + its channel neighbours
            reachable = adjacency.get(qm.id, set()) | {qm.id}
            if partner_qms:
                connectivity_score = len(reachable & partner_qms) / len(partner_qms)
            else:
                connectivity_score = 0.5  # neutral when no partners specified

            # Weighted composite
            total_score = (
                0.3 * nb_score
                + 0.3 * capacity_score
                + 0.4 * connectivity_score
            )

            scored.append({
                "qm_id": qm.id,
                "qm_name": qm.name,
                "total_score": round(total_score, 4),
                "neighborhood_score": round(nb_score, 4),
                "capacity_score": round(capacity_score, 4),
                "connectivity_score": round(connectivity_score, 4),
            })

        scored.sort(key=lambda s: s["total_score"], reverse=True)
        best = scored[0]

        return {
            "recommended_qm_id": best["qm_id"],
            "recommended_qm_name": best["qm_name"],
            "score_breakdown": {
                "neighborhood_affinity": best["neighborhood_score"],
                "capacity_headroom": best["capacity_score"],
                "connectivity": best["connectivity_score"],
                "total": best["total_score"],
            },
            "alternatives": scored[1:],
            "reason": (
                f"QM {best['qm_name']} scored highest ({best['total_score']}) "
                f"based on neighborhood affinity, capacity headroom, and "
                f"connectivity to communication partners."
            ),
        }

    # ------------------------------------------------------------------
    # Combined improvement calculation  (Req 24.5)
    # ------------------------------------------------------------------

    def calculate_combined_improvement(
        self, recommendations: list[dict]
    ) -> dict:
        """Calculate individual and combined complexity reduction.

        Each recommendation dict should contain at minimum:
        - ``type``: "consolidation" | "channel_elimination" | "clustering"
        - ``projected_channel_reduction``: int (channels removed)

        Returns a dict with:
        - ``individual``: list of per-recommendation reductions
        - ``combined_channel_reduction``: total channels removed
        - ``recommendation_count``: number of recommendations
        """
        individual: list[dict] = []
        total_channel_reduction = 0

        for i, rec in enumerate(recommendations):
            ch_reduction = rec.get("projected_channel_reduction", 0)
            total_channel_reduction += ch_reduction
            individual.append({
                "index": i,
                "type": rec.get("type", "unknown"),
                "channel_reduction": ch_reduction,
                "description": rec.get("description", rec.get("channel_name", "")),
            })

        return {
            "individual": individual,
            "combined_channel_reduction": total_channel_reduction,
            "recommendation_count": len(recommendations),
        }
