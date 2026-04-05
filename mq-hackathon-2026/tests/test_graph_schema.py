"""Unit tests for graph_store.schema — constraint and index definitions."""

from __future__ import annotations

from unittest.mock import MagicMock

from graph_store.schema import CONSTRAINTS, INDEXES, init_schema


class TestSchemaConstants:
    def test_constraint_names(self) -> None:
        names = [name for name, _ in CONSTRAINTS]
        assert names == ["qm_unique", "queue_unique", "app_unique", "channel_unique", "audit_unique"]

    def test_index_names(self) -> None:
        names = [name for name, _ in INDEXES]
        assert "idx_qm_lob" in names
        assert "idx_topology_version_tag" in names
        assert "idx_audit_correlation_id" in names

    def test_all_constraints_use_if_not_exists(self) -> None:
        for name, cypher in CONSTRAINTS:
            assert "IF NOT EXISTS" in cypher, f"{name} missing IF NOT EXISTS"

    def test_all_indexes_use_if_not_exists(self) -> None:
        for name, cypher in INDEXES:
            assert "IF NOT EXISTS" in cypher, f"{name} missing IF NOT EXISTS"


class TestInitSchema:
    def test_in_memory_mode_returns_all_names(self) -> None:
        result = init_schema(None)
        expected = [n for n, _ in CONSTRAINTS] + [n for n, _ in INDEXES]
        assert result == expected

    def test_neo4j_mode_runs_cypher(self) -> None:
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        result = init_schema(driver)
        expected = [n for n, _ in CONSTRAINTS] + [n for n, _ in INDEXES]
        assert result == expected
        assert session.run.call_count == len(CONSTRAINTS) + len(INDEXES)
