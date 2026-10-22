from __future__ import annotations

import pytest
from citi_bike_ingest.ingest.timestamp import TimestampConverter


def test_dst_normal_winter() -> None:
    conv = TimestampConverter()
    p = conv.parse_naive_ny("2026-01-15 12:34:56.789")
    assert p.utc_dt.tzinfo is not None
    assert p.is_ambiguous is False
    assert p.is_nonexistent is False


def test_dst_spring_forward_nonexistent() -> None:
    conv = TimestampConverter()
    with pytest.raises(ValueError):
        conv.parse_naive_ny("2026-03-08 02:30:00")


def test_dst_fall_back_ambiguous_flagged() -> None:
    conv = TimestampConverter()
    p = conv.parse_naive_ny("2026-11-01 01:30:00")
    assert p.utc_dt.tzinfo is not None
    assert p.is_ambiguous is True
