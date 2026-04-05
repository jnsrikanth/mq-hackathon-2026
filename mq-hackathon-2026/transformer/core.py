"""Transformer data models for CSV ingestion, topology representation, and complexity analysis.

Defines dataclasses and enums used by the CSV parser, topology transformer,
and complexity analyzer components:
- CSVValidationError: Exception for malformed CSV data
- ObjectType: Enum for MQ object types
- MQObject: Generic MQ object representation
- TopologySnapshot: Point-in-time topology state
- ComplexityMetric: Quantitative complexity scores
- ComplexityComparison: As-is vs target comparison
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CSVValidationError(Exception):
    """Raised when CSV contains malformed rows or missing columns.

    Attributes:
        file: Path to the CSV file that failed validation.
        row: Row number (1-based) where the error occurred.
        column: Column name that caused the error.
        message: Human-readable description of the validation failure.
    """

    def __init__(self, file: str, row: int, column: str, message: str) -> None:
        self.file = file
        self.row = row
        self.column = column
        self.message = message
        super().__init__(
            f"{file}:{row} column '{column}': {message}"
        )


class ObjectType(Enum):
    """Type of IBM MQ object within a topology snapshot."""

    QUEUE_MANAGER = "queue_manager"
    LOCAL_QUEUE = "local_queue"
    REMOTE_QUEUE = "remote_queue"
    TRANSMISSION_QUEUE = "transmission_queue"
    CHANNEL_SENDER = "channel_sender"
    CHANNEL_RECEIVER = "channel_receiver"
    APPLICATION = "application"


@dataclass
class MQObject:
    """A generic MQ object within a topology.

    Attributes:
        id: Unique identifier for this object.
        object_type: The type of MQ object.
        name: Human-readable name.
        owning_qm: Identifier of the queue manager that owns this object (if applicable).
        metadata: Arbitrary key-value pairs preserving original CSV attributes.
    """

    id: str
    object_type: ObjectType
    name: str
    owning_qm: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TopologySnapshot:
    """A point-in-time representation of an MQ topology.

    Attributes:
        snapshot_id: Unique identifier for this snapshot.
        timestamp: ISO-8601 timestamp of when the snapshot was captured.
        queue_managers: List of queue manager objects.
        queues: List of queue objects (local, remote, transmission).
        channels: List of channel objects (sender, receiver).
        applications: List of application objects.
        producer_consumer_map: Mapping of producer app IDs to lists of consumer app IDs.
    """

    snapshot_id: str
    timestamp: str
    queue_managers: list[MQObject] = field(default_factory=list)
    queues: list[MQObject] = field(default_factory=list)
    channels: list[MQObject] = field(default_factory=list)
    applications: list[MQObject] = field(default_factory=list)
    producer_consumer_map: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ComplexityMetric:
    """Quantitative complexity scores for a topology snapshot.

    Attributes:
        total_channels: Total number of channels in the topology.
        avg_routing_hops: Average number of routing hops between communicating app pairs.
        max_fan_in: Maximum inbound channel count on any single queue manager.
        max_fan_out: Maximum outbound channel count on any single queue manager.
        avg_fan_in: Average inbound channel count across all queue managers.
        avg_fan_out: Average outbound channel count across all queue managers.
        redundant_objects: Count of redundant or unused MQ objects.
        composite_score: Weighted aggregate complexity score.
    """

    total_channels: int
    avg_routing_hops: float
    max_fan_in: int
    max_fan_out: int
    avg_fan_in: float
    avg_fan_out: float
    redundant_objects: int
    composite_score: float


@dataclass
class ComplexityComparison:
    """Comparative complexity analysis between as-is and target topologies.

    Attributes:
        as_is: Complexity metric for the original topology.
        target: Complexity metric for the transformed target topology.
        channel_reduction: Absolute reduction in channel count.
        channel_reduction_pct: Percentage reduction in channel count.
        hop_reduction: Absolute reduction in average routing hops.
        hop_reduction_pct: Percentage reduction in average routing hops.
        redundant_reduction: Absolute reduction in redundant object count.
        composite_improvement_pct: Percentage improvement in composite score.
    """

    as_is: ComplexityMetric
    target: ComplexityMetric
    channel_reduction: int
    channel_reduction_pct: float
    hop_reduction: float
    hop_reduction_pct: float
    redundant_reduction: int
    composite_improvement_pct: float


# ---------------------------------------------------------------------------
# Real MQ topology CSV schema (enterprise dataset)
# ---------------------------------------------------------------------------

# The enterprise CSV has ~30 columns. Each row represents a queue-app
# relationship (the same queue appears twice: once for producer, once for
# consumer).  The transformer extracts QMs, queues, apps, and channels
# from these relationship rows.

MQ_CSV_COLUMNS: list[str] = [
    "Discrete Queue Name",
    "ProducerName",
    "ConsumerName",
    "Primary App_Full_Name",
    "PrimaryAppDisp",
    "PrimaryAppRole",
    "Primary Application Id",
    "q_type",                       # first q_type: app-perspective (Remote/Local)
    "Primary Neighborhood",
    "Primary Hosting Type",
    "Primary Data classification",
    "Primary Enterprise Critical Payment Application",
    "Primary PCI",
    "Primary Publicly Accessible",
    "Primary TRTC",
    "q_type",                       # second q_type: MQ-perspective (Remote/Local/Alias)
    "queue_manager_name",
    "app_id",
    "line_of_business",
    "cluster_name",
    "cluster_namelist",
    "def_persistence",
    "def_put_response",
    "inhibit_get",
    "inhibit_put",
    "remote_q_mgr_name",
    "remote_q_name",
    "usage",
    "xmit_q_name",
    "Neighborhood",
]

# Minimum required columns for the parser to extract topology objects
MQ_CSV_REQUIRED: list[str] = [
    "Discrete Queue Name",
    "PrimaryAppRole",
    "queue_manager_name",
    "app_id",
]

# Valid values for constrained columns in the enterprise dataset
_VALID_VALUES: dict[str, set[str]] = {
    "PrimaryAppRole": {"producer", "consumer"},
    # Legacy generic column constraints (backward compat with unit tests)
    "queue_type": {"local", "remote", "transmission"},
    "direction": {"sender", "receiver"},
    "role": {"producer", "consumer", "both"},
}

# Legacy generic schemas kept for backward compatibility with unit tests
_CSV_SCHEMAS: dict[str, dict[str, str]] = {
    "queue_manager": {"id": "str", "name": "str", "hostname": "str", "port": "int"},
    "queue": {"id": "str", "name": "str", "queue_type": "str", "owning_qm_id": "str"},
    "channel": {"id": "str", "name": "str", "direction": "str", "from_qm_id": "str", "to_qm_id": "str"},
    "application": {"id": "str", "name": "str", "connected_qm_id": "str", "role": "str"},
}


class CSVParser:
    """Parses and validates CSV files into structured row dictionaries.

    Designed for Bootstrap_Mode ingestion of legacy MQ topology CSV data.
    Uses only the Python standard-library ``csv`` module — no external
    dependencies required.
    """

    def parse_file(
        self,
        filepath: str,
        expected_columns: list[str],
        *,
        type_specs: dict[str, str] | None = None,
    ) -> list[dict]:
        """Parse a single CSV file with column validation.

        Args:
            filepath: Path to the CSV file.
            expected_columns: Column names that *must* be present in the header.
            type_specs: Optional mapping of ``column_name -> type_name``
                (``"int"``, ``"float"``, ``"str"``) used for per-cell type
                validation.  When ``None``, only presence checks are performed.

        Returns:
            A list of row dictionaries (one per CSV data row).

        Raises:
            CSVValidationError: On missing expected columns or malformed rows.
        """
        rows: list[dict] = []

        with open(filepath, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)

            # --- header / column validation ---
            if reader.fieldnames is None:
                raise CSVValidationError(
                    file=filepath,
                    row=1,
                    column="<header>",
                    message="CSV file is empty or has no header row",
                )

            header_set = set(reader.fieldnames)
            for col in expected_columns:
                if col not in header_set:
                    raise CSVValidationError(
                        file=filepath,
                        row=1,
                        column=col,
                        message=f"Missing required column '{col}'",
                    )

            # --- row-level parsing & validation ---
            for row_num_0, row in enumerate(reader):
                # csv.DictReader uses 0-based iteration; row 1 is the header,
                # so data rows start at 2.
                row_num = row_num_0 + 2
                self.validate_row(
                    row,
                    row_num=row_num,
                    file=filepath,
                    expected_columns=expected_columns,
                    type_specs=type_specs,
                )
                rows.append(row)

        return rows

    # ------------------------------------------------------------------
    # Row-level validation
    # ------------------------------------------------------------------

    def validate_row(
        self,
        row: dict,
        row_num: int,
        file: str,
        expected_columns: list[str] | None = None,
        type_specs: dict[str, str] | None = None,
    ) -> None:
        """Validate a single CSV row for required fields and data types.

        Args:
            row: The row dictionary produced by ``csv.DictReader``.
            row_num: 1-based row number (for error reporting).
            file: Source file path (for error reporting).
            expected_columns: Columns that must have non-empty values.
            type_specs: Optional ``column_name -> type_name`` mapping for
                type coercion checks (``"int"``, ``"float"``, ``"str"``).

        Raises:
            CSVValidationError: On missing/empty required fields or type
                coercion failures.
        """
        if expected_columns is None:
            expected_columns = list(row.keys())

        # 1. Required-field presence check
        for col in expected_columns:
            value = row.get(col)
            if value is None or str(value).strip() == "":
                raise CSVValidationError(
                    file=file,
                    row=row_num,
                    column=col,
                    message=f"Required field '{col}' is missing or empty",
                )

        # 2. Type coercion checks
        if type_specs:
            for col, expected_type in type_specs.items():
                value = row.get(col)
                if value is None:
                    # Not a required column — skip if absent
                    continue
                value_str = str(value).strip()
                if value_str == "":
                    continue

                if expected_type == "int":
                    try:
                        int(value_str)
                    except ValueError:
                        raise CSVValidationError(
                            file=file,
                            row=row_num,
                            column=col,
                            message=f"Expected integer value, got '{value_str}'",
                        )
                elif expected_type == "float":
                    try:
                        float(value_str)
                    except ValueError:
                        raise CSVValidationError(
                            file=file,
                            row=row_num,
                            column=col,
                            message=f"Expected float value, got '{value_str}'",
                        )
                # "str" always passes — any value is a valid string.

        # 3. Enum / constrained-value checks
        for col, valid_set in _VALID_VALUES.items():
            value = row.get(col)
            if value is not None and str(value).strip() != "":
                if str(value).strip().lower() not in valid_set:
                    raise CSVValidationError(
                        file=file,
                        row=row_num,
                        column=col,
                        message=(
                            f"Invalid value '{value}' for column '{col}'; "
                            f"expected one of {sorted(valid_set)}"
                        ),
                    )
