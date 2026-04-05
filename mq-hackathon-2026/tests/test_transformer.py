"""Tests for the integrated transformer pipeline.

Tests the real enterprise data flow:
  AS-IS CSV → read flows → normalize → build target edges → validate → write artifacts
"""

from __future__ import annotations

import os
import tempfile

import pytest

from transformer.validators import build_edges_target, normalize_app, qm_for, validate_edges_target
from transformer.analyzer import analyze_topology, edges_to_dot
from transformer.transform import (
    compute_target_metrics,
    read_as_is_csv,
    run_transform,
    unique_flows,
    write_channels_csv,
    write_edges_csv,
)


# ---------------------------------------------------------------------------
# Sample enterprise CSV data
# ---------------------------------------------------------------------------

SAMPLE_CSV = """\
Discrete Queue Name,ProducerName,ConsumerName,PrimaryAppRole,queue_manager_name,app_id,line_of_business,q_type,remote_q_mgr_name,remote_q_name,xmit_q_name,Neighborhood
8A.OK.VFHVIGL.HDF.RQST,DFCeth - HUVY MIPKNVL ZYU,OOIKN JIU,Producer,WL6EX2C,8A,TECHCT,Remote,WQ26,8A.OK.VFHVIGL.HDF.RQST.XA21,OK.WQ26,Mainframe
8A.OK.VFHVIGL.HDF.RQST,DFCeth - HUVY MIPKNVL ZYU,OOIKN JIU,Consumer,WL6EX2C,OK,TECHCT,Remote,WQ26,8A.OK.VFHVIGL.HDF.RQST.XA21,OK.WQ26,Mainframe
8AFK.PPCSM.ZEEWALA.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Consumer,WL6ER2D,PPCSM,TECHCCIBT,Local,,,, Wholesale Banking
8AFK.PPCSM.ZEEWALA.ACK,Hdc - Hdxknvlr Kavk,PP Arcne VMU,Producer,WL6ER2D,8AFK,TECHCCIBT,Local,,,,Wholesale Banking
"""


def _write_csv(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class TestNormalizeApp:
    def test_basic(self):
        assert normalize_app("  hello   world  ") == "HELLO WORLD"

    def test_empty(self):
        assert normalize_app("") == ""

    def test_already_upper(self):
        assert normalize_app("APP1") == "APP1"


class TestQmFor:
    def test_deterministic(self):
        qm1 = qm_for("APP1")
        qm2 = qm_for("APP1")
        assert qm1 == qm2
        assert qm1.startswith("QM_")

    def test_different_apps_different_qms(self):
        assert qm_for("APP1") != qm_for("APP2")

    def test_empty(self):
        assert qm_for("") == ""

    def test_case_insensitive(self):
        assert qm_for("app1") == qm_for("APP1")


class TestBuildEdgesTarget:
    def test_single_flow(self):
        edges = build_edges_target([("AppA", "AppB")])
        assert len(edges) == 3
        rels = [r for _, _, r in edges]
        assert "writes_to" in rels
        assert "transit_via_channel_pair" in rels
        assert "reads_from" in rels

    def test_deduplication(self):
        edges = build_edges_target([("AppA", "AppB"), ("AppA", "AppB")])
        assert len(edges) == 3  # still 3, not 6

    def test_multiple_flows(self):
        edges = build_edges_target([("A", "B"), ("A", "C")])
        # A->QM_A (writes_to) shared, QM_A->QM_B, QM_A->QM_C, QM_B->B, QM_C->C
        assert len(edges) >= 5


class TestValidateEdgesTarget:
    def test_valid_edges_pass(self):
        edges = build_edges_target([("AppA", "AppB")])
        ok, errs, details = validate_edges_target(edges)
        assert ok is True
        assert errs == []

    def test_invalid_writes_to_detected(self):
        # Manually create a bad edge: QM writes_to QM
        edges = [("QM_1234", "QM_5678", "writes_to")]
        ok, errs, _ = validate_edges_target(edges)
        assert ok is False
        assert any("writes_to must be App->QM" in e for e in errs)

    def test_invalid_reads_from_detected(self):
        edges = [("AppA", "QM_1234", "reads_from")]
        ok, errs, _ = validate_edges_target(edges)
        assert ok is False
        assert any("reads_from must be QM->App" in e for e in errs)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class TestAnalyzer:
    def test_analyze_sample_csv(self):
        path = _write_csv(SAMPLE_CSV)
        try:
            metrics, extras = analyze_topology(path)
            assert metrics["total_nodes"] > 0
            assert metrics["total_edges"] > 0
            assert metrics["unique_queue_managers"] > 0
            assert isinstance(metrics["top5_hub_qm"], list)
            assert isinstance(extras["edges"], list)
        finally:
            os.unlink(path)

    def test_edges_to_dot(self):
        edges = [("AppA", "QM_1", "writes_to"), ("QM_1", "AppB", "reads_from")]
        dot = edges_to_dot(edges)
        assert "digraph MQ" in dot
        assert "AppA" in dot
        assert "QM_1" in dot


# ---------------------------------------------------------------------------
# Transform pipeline
# ---------------------------------------------------------------------------

class TestReadAsIsCsv:
    def test_reads_flows(self):
        path = _write_csv(SAMPLE_CSV)
        try:
            flows, header = read_as_is_csv(path)
            assert len(flows) > 0
            assert "ProducerName" in header
            assert "ConsumerName" in header
            # Each flow is (producer, consumer)
            for p, c in flows:
                assert p != ""
                assert c != ""
        finally:
            os.unlink(path)


class TestUniqueFlows:
    def test_deduplicates(self):
        flows = [("App A", "App B"), ("APP A", "APP B"), ("App C", "App D")]
        result = unique_flows(flows)
        assert len(result) == 2  # first two collapse

    def test_preserves_order(self):
        flows = [("X", "Y"), ("A", "B")]
        result = unique_flows(flows)
        assert result[0] == (normalize_app("X"), normalize_app("Y"))


class TestRunTransform:
    def test_full_pipeline(self):
        csv_path = _write_csv(SAMPLE_CSV)
        out_dir = tempfile.mkdtemp()
        try:
            result = run_transform(csv_path, out_dir)
            assert result["ok"] is True
            assert "artifacts" in result
            assert "target_report" in result
            assert "target_dot" in result

            # Check artifacts were written
            assert os.path.exists(result["artifacts"]["edges_target"])
            assert os.path.exists(result["artifacts"]["channels"])
            assert os.path.exists(result["artifacts"]["target_csv"])

            # Check metrics
            metrics = result["target_report"]["topology_metrics"]
            assert metrics["total_nodes"] > 0
            assert metrics["total_edges"] > 0
            assert metrics["brittle_apps"] == 0  # by construction
        finally:
            os.unlink(csv_path)

    def test_target_csv_has_same_columns_as_input(self):
        csv_path = _write_csv(SAMPLE_CSV)
        out_dir = tempfile.mkdtemp()
        try:
            result = run_transform(csv_path, out_dir)
            # Read back the target CSV and check columns
            import csv as csv_mod
            with open(result["artifacts"]["target_csv"], newline="") as f:
                reader = csv_mod.DictReader(f)
                target_header = reader.fieldnames
            # Should have the same columns as input
            _, input_header = read_as_is_csv(csv_path)
            assert set(target_header) == set(input_header)
        finally:
            os.unlink(csv_path)

    def test_target_edges_pass_validation(self):
        csv_path = _write_csv(SAMPLE_CSV)
        out_dir = tempfile.mkdtemp()
        try:
            result = run_transform(csv_path, out_dir)
            # Read back edges and validate
            import csv as csv_mod
            edges = []
            with open(result["artifacts"]["edges_target"], newline="") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    edges.append((row["src"], row["dst"], row["relation"]))
            ok, errs, _ = validate_edges_target(edges)
            assert ok is True, f"Validation errors: {errs}"
        finally:
            os.unlink(csv_path)


class TestComputeTargetMetrics:
    def test_basic_metrics(self):
        edges = build_edges_target([("A", "B"), ("C", "D")])
        metrics = compute_target_metrics(edges)
        assert metrics["total_nodes"] > 0
        assert metrics["total_edges"] == len(edges)
        assert metrics["unique_queue_managers"] > 0
        assert metrics["brittle_apps"] == 0
