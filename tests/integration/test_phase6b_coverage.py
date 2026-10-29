"""Exercise the real dbt mart SQL with synthetic observations in temporary tables."""
import os
from pathlib import Path

from jinja2 import Environment, StrictUndefined
import psycopg
import pytest

from citi_bike_ingest.config.settings import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("CITI_TEST_POSTGRES") != "1", reason="Explicit local PostgreSQL opt-in"
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def coverage_db():
    with psycopg.connect(Settings().database_url) as conn:
        conn.execute("create temp table coverage_windows "
                     "(coverage_start timestamptz, coverage_end timestamptz, window_label text)")
        conn.execute("create temp table coverage_gaps (gap_start timestamptz, gap_end timestamptz)")
        conn.execute("create temp table stg_trips (started_at_utc timestamptz, "
                     "started_at_status text, member_casual text)")
        conn.execute("insert into coverage_windows values "
                     "('2025-01-01 05:00Z','2025-02-01 05:00Z','2025-01'),"
                     "('2025-03-01 05:00Z','2025-04-01 04:00Z','2025-03'),"
                     "('2025-11-01 04:00Z','2025-12-01 05:00Z','2025-11'),"
                     "('2026-01-01 05:00Z','2026-02-01 05:00Z','2026-01')")
        conn.execute("insert into coverage_gaps values "
                     "('2026-01-25 17:00Z','2026-01-26 12:00Z')")
        conn.execute("insert into stg_trips values "
                     "('2025-01-01 05:03Z','valid','member'),"
                     "('2025-01-31 05:03Z','valid','casual'),"
                     "('2026-01-25 17:03Z','valid','member'),"
                     "('2026-01-26 12:03Z','valid','member')")
        env = Environment(undefined=StrictUndefined)
        env.globals.update(ref=lambda name: name, var=lambda name: "America/New_York")
        macro = (ROOT / "dbt/macros/coverage_spine.sql").read_text()
        for name in ("fct_hourly_demand", "fct_hourly_coverage"):
            source = (ROOT / f"dbt/models/marts/{name}.sql").read_text()
            query = env.from_string(macro + "\n" + source).render()
            conn.execute(f"create temp table {name} as {query}")
        yield conn
        conn.rollback()


def test_unexplained_empty_hour_stays_missing_including_month_end(coverage_db):
    c = coverage_db
    for hour in ("2025-01-01 06:00Z", "2025-01-31 06:00Z"):
        assert c.execute("select coverage_status,coverage_reason,in_demand_mart,"
                         "demand_rides_total from fct_hourly_coverage where hour_utc=%s",
                         (hour,)).fetchone() == (
                             "uncertain", "unexplained_source_gap", False, None)
        assert c.execute("select count(*) from fct_hourly_demand where hour_utc=%s",
                         (hour,)).fetchone() == (0,)
    assert c.execute("select coverage_status,demand_rides_total from fct_hourly_coverage "
                     "where hour_utc='2025-01-31 05:00Z'").fetchone() == ("boundary", 1)


def test_dst_and_verified_outage_keep_distinct_semantics(coverage_db):
    c = coverage_db
    assert c.execute("select count(*) from fct_hourly_coverage "
                     "where hour_local_date='2025-03-09'").fetchone() == (23,)
    assert c.execute("select count(*) from fct_hourly_coverage "
                     "where hour_local_date='2025-11-02'").fetchone() == (25,)
    assert c.execute("select count(*) from fct_hourly_coverage "
                     "where coverage_reason='dst_ambiguous_hour' "
                     "and coverage_status='uncertain' and demand_rides_total is null "
                     "and not in_demand_mart").fetchone() == (2,)
    assert c.execute("select count(*) from fct_hourly_coverage "
                     "where coverage_status='unavailable'").fetchone() == (18,)
    assert c.execute("select coverage_reason,in_demand_mart,demand_rides_total "
                     "from fct_hourly_coverage where hour_utc='2026-01-25 17:00Z'"
                     ).fetchone() == ("gap_onset", False, None)
    assert c.execute("select coverage_reason,in_demand_mart,demand_rides_total "
                     "from fct_hourly_coverage where hour_utc='2026-01-26 12:00Z'"
                     ).fetchone() == ("gap_resume", True, 1)
