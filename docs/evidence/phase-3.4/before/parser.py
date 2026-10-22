from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

MODERN_SCHEMA = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
]


@dataclass
class ParseResult:
    """Result of parsing a row."""

    accepted: bool
    row_data: Optional[Dict[str, str]] = None
    reason: Optional[str] = None


@dataclass
class BatchStats:
    """Batch processing statistics."""

    rows_read: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    reasons: Dict[str, int] = field(default_factory=dict)


class ZipCSVParser:
    """Parse CSV members from ZIP archive."""

    def __init__(self, zip_path: Path) -> None:
        self.zip_path = zip_path
        self._seen_ride_ids: set[str] = set()

    def get_csv_members(self) -> List[str]:
        """Get CSV members, ignoring hidden/dirs/__MACOSX."""
        members = []
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                basename = Path(name).name
                if basename.startswith("._") or basename.startswith("."):
                    continue
                if "__MACOSX" in name:
                    continue
                if name.lower().endswith(".csv"):
                    members.append(name)
        return sorted(members)

    def validate_header(self, header: List[str]) -> Tuple[bool, List[str]]:
        """Validate header against modern schema."""
        # Strip quotes and whitespace
        clean = [h.strip().strip('"').strip("'") for h in header]
        required = set(MODERN_SCHEMA)
        present = set(clean)
        missing = required - present
        if missing:
            return False, sorted(list(missing))
        return True, clean

    def parse_row(self, row: Dict[str, str], member: str) -> ParseResult:
        """Parse and validate a single row."""
        # Check required fields
        ride_id = row.get("ride_id", "").strip()
        if not ride_id:
            return ParseResult(False, None, "missing_ride_id")

        started_at = row.get("started_at", "").strip()
        ended_at = row.get("ended_at", "").strip()
        if not started_at:
            return ParseResult(False, None, "invalid_start_timestamp")
        if not ended_at:
            return ParseResult(False, None, "invalid_end_timestamp")

        # Check duplicates
        if ride_id in self._seen_ride_ids:
            return ParseResult(False, None, "duplicate_ride_id")
        self._seen_ride_ids.add(ride_id)

        # Clean station IDs (preserve as strings)
        start_station_id = row.get("start_station_id", "").strip()
        end_station_id = row.get("end_station_id", "").strip()
        # Don't discard if missing - flag but allow? Policy says don't blindly discard
        # But we record nulls

        # Basic duration check (will be validated properly later with timestamps)
        try:
            # Quick check
            pass
        except Exception:
            pass

        # Clean row data
        cleaned = {}
        for k in MODERN_SCHEMA:
            val = row.get(k, "")
            if val is None:
                cleaned[k] = ""
            else:
                cleaned[k] = str(val).strip().strip('"').strip("'")
        cleaned["_source_member"] = member

        return ParseResult(True, cleaned, None)

    def iterate_rows(self, batch_size: int = 50000) -> Iterator[List[Tuple[Dict[str, str], str]]]:
        """Iterate rows in batches."""
        members = self.get_csv_members()
        if not members:
            raise ValueError("No CSV members found in archive")

        batch = []
        for member in members:
            with zipfile.ZipFile(self.zip_path, "r") as zf:
                with zf.open(member) as f:
                    text_stream = io.TextIOWrapper(f, encoding="utf-8")
                    reader = csv.DictReader(text_stream)
                    # Validate header on first member
                    if reader.fieldnames:
                        valid, clean_header = self.validate_header(reader.fieldnames)
                        if not valid:
                            raise ValueError(f"Invalid header in {member}: missing {valid}")
                    for row in reader:
                        result = self.parse_row(row, member)
                        if result.accepted and result.row_data:
                            batch.append((result.row_data, member))
                        else:
                            # Store rejected - we'll handle separately
                            batch.append(({"_rejected": "true", "_reason": result.reason or "unknown", **row}, member))
                        if len(batch) >= batch_size:
                            yield batch
                            batch = []
        if batch:
            yield batch
