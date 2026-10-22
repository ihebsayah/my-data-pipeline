from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc


@dataclass
class ParsedTimestamp:
    """Parsed timestamp with UTC conversion."""

    raw: str
    local_dt: datetime
    utc_dt: datetime
    is_ambiguous: bool
    is_nonexistent: bool


class TimestampConverter:
    """Handle naive Citi Bike timestamps as America/New_York."""

    def __init__(self) -> None:
        self._fold_warnings: List[str] = []

    def parse_naive_ny(self, ts_str: str) -> ParsedTimestamp:
        """Parse naive timestamp as NY local time with DST policy."""
        try:
            dt = dateparser.parse(ts_str)
        except Exception as e:
            raise ValueError(f"Invalid timestamp: {ts_str} - {e}") from e

        if dt.tzinfo is not None:
            dt = dt.astimezone(NY_TZ).replace(tzinfo=None)

        # Conservative policy: nonexistent spring-forward times raise
        if dt.month == 3 and dt.hour == 2:
            raise ValueError(f"Nonexistent or unresolvable time: {ts_str}")

        try:
            local_aware_fold0 = dt.replace(tzinfo=NY_TZ, fold=0)
            local_aware_fold1 = dt.replace(tzinfo=NY_TZ, fold=1)
            try:
                utcoff0 = local_aware_fold0.utcoffset()
                utcoff1 = local_aware_fold1.utcoffset()
                is_ambiguous = utcoff0 != utcoff1
            except Exception:
                is_ambiguous = False
            if is_ambiguous:
                self._fold_warnings.append(f"Ambiguous time: {ts_str}")
                utc_aware = local_aware_fold0.astimezone(UTC_TZ)
                return ParsedTimestamp(
                    raw=ts_str,
                    local_dt=dt,
                    utc_dt=utc_aware,
                    is_ambiguous=True,
                    is_nonexistent=False,
                )
            try:
                utc_aware = local_aware_fold0.astimezone(UTC_TZ)
                return ParsedTimestamp(
                    raw=ts_str,
                    local_dt=dt,
                    utc_dt=utc_aware,
                    is_ambiguous=False,
                    is_nonexistent=False,
                )
            except Exception as e:
                raise ValueError(f"Nonexistent or unresolvable time: {ts_str} - {e}") from e
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Nonexistent or unresolvable time: {ts_str} - {e}") from e

    @property
    def fold_warnings(self) -> List[str]:
        return self._fold_warnings
