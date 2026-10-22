from __future__ import annotations

from citi_bike_ingest.ingest.timestamp import TimestampConverter


def test_parse_naive_ny() -> None:
    conv = TimestampConverter()
    p = conv.parse_naive_ny("2026-01-01 05:36:24.539")
    assert p.utc_dt.tzinfo is not None


def test_parse_with_milliseconds() -> None:
    conv = TimestampConverter()
    p = conv.parse_naive_ny("2026-07-01 12:00:00.123")
    assert p.utc_dt is not None
