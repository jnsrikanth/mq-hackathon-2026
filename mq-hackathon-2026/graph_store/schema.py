"""Graph schema definitions for the MQ Guardian Platform.

Declares the constraints and indexes that would be applied to a Neo4j
instance.  For the in-memory GraphStore these serve as documentation and
are validated structurally.  When migrating to Neo4j, ``init_schema``
accepts a Neo4j driver and runs the Cypher statements directly.

Requirements: 2.1, 2.4
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Uniqueness constraints (design.md §2 — Neo4j Schema)
# ---------------------------------------------------------------------------

CONSTRAINTS: list[tuple[str, str]] = [
    (
        "qm_unique",
        "CREATE CONSTRAINT qm_unique IF NOT EXISTS "
        "FOR (qm:QueueManager) REQUIRE qm.id IS UNIQUE",
    ),
    (
        "queue_unique",
        "CREATE CONSTRAINT queue_unique IF NOT EXISTS "
        "FOR (q:Queue) REQUIRE q.id IS UNIQUE",
    ),
    (
        "app_unique",
        "CREATE CONSTRAINT app_unique IF NOT EXISTS "
        "FOR (a:Application) REQUIRE a.id IS UNIQUE",
    ),
    (
        "channel_unique",
        "CREATE CONSTRAINT channel_unique IF NOT EXISTS "
        "FOR (c:Channel) REQUIRE c.id IS UNIQUE",
    ),
    (
        "audit_unique",
        "CREATE CONSTRAINT audit_unique IF NOT EXISTS "
        "FOR (au:AuditEvent) REQUIRE au.id IS UNIQUE",
    ),
]

# ---------------------------------------------------------------------------
# Indexes for common query patterns
# ---------------------------------------------------------------------------

INDEXES: list[tuple[str, str]] = [
    (
        "idx_qm_lob",
        "CREATE INDEX idx_qm_lob IF NOT EXISTS FOR (qm:QueueManager) ON (qm.lob)",
    ),
    (
        "idx_queue_lob",
        "CREATE INDEX idx_queue_lob IF NOT EXISTS FOR (q:Queue) ON (q.lob)",
    ),
    (
        "idx_app_lob",
        "CREATE INDEX idx_app_lob IF NOT EXISTS FOR (a:Application) ON (a.lob)",
    ),
    (
        "idx_channel_lob",
        "CREATE INDEX idx_channel_lob IF NOT EXISTS FOR (c:Channel) ON (c.lob)",
    ),
    (
        "idx_topology_version_tag",
        "CREATE INDEX idx_topology_version_tag IF NOT EXISTS "
        "FOR (tv:TopologyVersion) ON (tv.tag)",
    ),
    (
        "idx_audit_correlation_id",
        "CREATE INDEX idx_audit_correlation_id IF NOT EXISTS "
        "FOR (au:AuditEvent) ON (au.correlation_id)",
    ),
]


def init_schema(driver: Any) -> list[str]:
    """Run all Cypher constraints and indexes against the given Neo4j driver.

    For the in-memory GraphStore, pass ``None`` and this returns the list
    of schema item names without executing Cypher.  When a real Neo4j
    driver is provided, the statements are executed via a session.
    """
    executed: list[str] = []

    if driver is None:
        # In-memory mode: just return the names
        for name, _ in CONSTRAINTS:
            executed.append(name)
        for name, _ in INDEXES:
            executed.append(name)
        logger.info("Schema definitions loaded (in-memory mode) — %d items.", len(executed))
        return executed

    # Neo4j mode: execute Cypher
    with driver.session() as session:
        for name, cypher in CONSTRAINTS:
            logger.info("Creating constraint: %s", name)
            session.run(cypher)
            executed.append(name)

        for name, cypher in INDEXES:
            logger.info("Creating index: %s", name)
            session.run(cypher)
            executed.append(name)

    logger.info("Schema initialisation complete — %d items applied.", len(executed))
    return executed
