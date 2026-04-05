"""Unit tests for CSVParser (transformer/core.py).

Validates Requirements 1.1, 1.3, 1.5:
- Parse CSV files into structured data within 30s for up to 14K rows
- Reject malformed rows / missing columns with descriptive errors
- Accept QM, queue, channel, application CSV data with optional metadata
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from transformer.core import CSVParser, CSVValidationError


@pytest.fixture
def parser() -> CSVParser:
    return CSVParser()


def _write_csv(content: str) -> str:
    """Write CSV content to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        f.write(content)
    return path


# ------------------------------------------------------------------
# Happy-path tests
# ------------------------------------------------------------------


class TestParseFileHappyPath:
    def test_parse_simple_csv(self, parser: CSVParser) -> None:
        path = _write_csv("id,name,hostname,port\nQM1,Manager1,host1,1414\n")
        try:
            rows = parser.parse_file(path, ["id", "name", "hostname", "port"])
            assert len(rows) == 1
            assert rows[0]["id"] == "QM1"
            assert rows[0]["port"] == "1414"
        finally:
            os.unlink(path)

    def test_parse_multiple_rows(self, parser: CSVParser) -> None:
        csv = "id,name,hostname,port\n"
        csv += "QM1,M1,h1,1414\n"
        csv += "QM2,M2,h2,1415\n"
        csv += "QM3,M3,h3,1416\n"
        path = _write_csv(csv)
        try:
            rows = parser.parse_file(path, ["id", "name"])
            assert len(rows) == 3
        finally:
            os.unlink(path)

    def test_extra_columns_preserved(self, parser: CSVParser) -> None:
        path = _write_csv("id,name,region,lob\nQM1,M1,us-east,retail\n")
        try:
            rows = parser.parse_file(path, ["id", "name"])
            assert rows[0]["region"] == "us-east"
            assert rows[0]["lob"] == "retail"
        finally:
            os.unlink(path)

    def test_type_specs_int_validation_passes(self, parser: CSVParser) -> None:
        path = _write_csv("id,name,hostname,port\nQM1,M1,h1,1414\n")
        try:
            rows = parser.parse_file(
                path, ["id", "name", "hostname", "port"], type_specs={"port": "int"}
            )
            assert len(rows) == 1
        finally:
            os.unlink(path)

    def test_queue_csv_with_valid_queue_type(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,queue_type,owning_qm_id\nQ1,Queue1,local,QM1\n"
        )
        try:
            rows = parser.parse_file(path, ["id", "name", "queue_type", "owning_qm_id"])
            assert rows[0]["queue_type"] == "local"
        finally:
            os.unlink(path)

    def test_application_csv_with_valid_role(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,connected_qm_id,role\nAPP1,App1,QM1,producer\n"
        )
        try:
            rows = parser.parse_file(path, ["id", "name", "connected_qm_id", "role"])
            assert rows[0]["role"] == "producer"
        finally:
            os.unlink(path)

    def test_channel_csv_with_valid_direction(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,direction,from_qm_id,to_qm_id\nCH1,Chan1,sender,QM1,QM2\n"
        )
        try:
            rows = parser.parse_file(
                path, ["id", "name", "direction", "from_qm_id", "to_qm_id"]
            )
            assert rows[0]["direction"] == "sender"
        finally:
            os.unlink(path)


# ------------------------------------------------------------------
# Error / rejection tests (Req 1.3)
# ------------------------------------------------------------------


class TestParseFileErrors:
    def test_missing_required_column_raises(self, parser: CSVParser) -> None:
        path = _write_csv("id,name\nQM1,M1\n")
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "name", "hostname"])
            assert exc_info.value.column == "hostname"
            assert exc_info.value.row == 1
        finally:
            os.unlink(path)

    def test_empty_file_raises(self, parser: CSVParser) -> None:
        path = _write_csv("")
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id"])
            assert "empty" in exc_info.value.message.lower() or "header" in exc_info.value.message.lower()
        finally:
            os.unlink(path)

    def test_empty_required_field_raises(self, parser: CSVParser) -> None:
        path = _write_csv("id,name,hostname,port\nQM1,,h1,1414\n")
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "name", "hostname", "port"])
            assert exc_info.value.column == "name"
            assert exc_info.value.row == 2
        finally:
            os.unlink(path)

    def test_invalid_int_type_raises(self, parser: CSVParser) -> None:
        path = _write_csv("id,name,hostname,port\nQM1,M1,h1,notanumber\n")
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(
                    path, ["id", "name", "hostname", "port"], type_specs={"port": "int"}
                )
            assert exc_info.value.column == "port"
            assert "integer" in exc_info.value.message.lower()
        finally:
            os.unlink(path)

    def test_invalid_float_type_raises(self, parser: CSVParser) -> None:
        path = _write_csv("id,score\nQM1,abc\n")
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "score"], type_specs={"score": "float"})
            assert exc_info.value.column == "score"
            assert "float" in exc_info.value.message.lower()
        finally:
            os.unlink(path)

    def test_invalid_queue_type_raises(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,queue_type,owning_qm_id\nQ1,Queue1,invalid_type,QM1\n"
        )
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "name", "queue_type", "owning_qm_id"])
            assert exc_info.value.column == "queue_type"
        finally:
            os.unlink(path)

    def test_invalid_role_raises(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,connected_qm_id,role\nAPP1,App1,QM1,invalid_role\n"
        )
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "name", "connected_qm_id", "role"])
            assert exc_info.value.column == "role"
        finally:
            os.unlink(path)

    def test_invalid_direction_raises(self, parser: CSVParser) -> None:
        path = _write_csv(
            "id,name,direction,from_qm_id,to_qm_id\nCH1,C1,bidirectional,QM1,QM2\n"
        )
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(
                    path, ["id", "name", "direction", "from_qm_id", "to_qm_id"]
                )
            assert exc_info.value.column == "direction"
        finally:
            os.unlink(path)

    def test_error_reports_correct_row_number(self, parser: CSVParser) -> None:
        csv = "id,name\nQM1,M1\nQM2,\n"
        path = _write_csv(csv)
        try:
            with pytest.raises(CSVValidationError) as exc_info:
                parser.parse_file(path, ["id", "name"])
            # Row 3 in the file (header=1, data row 1=2, data row 2=3)
            assert exc_info.value.row == 3
        finally:
            os.unlink(path)


# ------------------------------------------------------------------
# Performance test (Req 1.1 — 14K rows in <30s)
# ------------------------------------------------------------------


class TestParseFilePerformance:
    def test_14k_rows_under_30_seconds(self, parser: CSVParser) -> None:
        lines = ["id,name,hostname,port"]
        for i in range(14_000):
            lines.append(f"QM{i},Manager{i},host{i},{1414 + i % 100}")
        path = _write_csv("\n".join(lines) + "\n")
        try:
            start = time.monotonic()
            rows = parser.parse_file(
                path,
                ["id", "name", "hostname", "port"],
                type_specs={"port": "int"},
            )
            elapsed = time.monotonic() - start
            assert len(rows) == 14_000
            assert elapsed < 30.0, f"Parsing took {elapsed:.2f}s, exceeds 30s limit"
        finally:
            os.unlink(path)


# ------------------------------------------------------------------
# validate_row standalone tests
# ------------------------------------------------------------------


class TestValidateRow:
    def test_valid_row_passes(self, parser: CSVParser) -> None:
        row = {"id": "QM1", "name": "Manager1"}
        # Should not raise
        parser.validate_row(row, row_num=2, file="test.csv", expected_columns=["id", "name"])

    def test_missing_field_raises(self, parser: CSVParser) -> None:
        row = {"id": "QM1", "name": ""}
        with pytest.raises(CSVValidationError):
            parser.validate_row(row, row_num=2, file="test.csv", expected_columns=["id", "name"])

    def test_type_spec_int_valid(self, parser: CSVParser) -> None:
        row = {"id": "QM1", "port": "1414"}
        parser.validate_row(
            row, row_num=2, file="test.csv",
            expected_columns=["id"], type_specs={"port": "int"},
        )

    def test_type_spec_int_invalid(self, parser: CSVParser) -> None:
        row = {"id": "QM1", "port": "abc"}
        with pytest.raises(CSVValidationError):
            parser.validate_row(
                row, row_num=2, file="test.csv",
                expected_columns=["id"], type_specs={"port": "int"},
            )
