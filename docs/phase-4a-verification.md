# Phase 4A Verification — dbt Transformation Layer

**Date:** 2026-01 (verification run); dbt-core 1.12.6, dbt-postgres 1.11.0, PostgreSQL 16 (Docker, session timezone `Etc/UTC`).

This report documents the implementation and real-data verification of the dbt Core
transformation layer: `raw.trips` → `staging.stg_trips` → `marts.fct_hourly_demand`.
All numbers below are actual outputs from the live local database (queries included).

---

## 1. Implementation summary

| Component | Path | Materialization | Notes |
|---|---|---|---|
| Project | `dbt/dbt_project.yml` | — | profile `urban_mobility`, models → schemas `staging` / `marts`, coverage + timezone vars |
| Profiles | `dbt/profiles.yml` | — | env-var credentials (`POSTGRES_*`) with `.env.example`-compatible defaults |
| Schema override | `dbt/macros/generate_schema_name.sql` | — | custom schemas used verbatim (`staging`, `marts`) |
| DST/timestamp macros | `dbt/macros/ny_timestamps.sql` | — | `ny_local_ts`, `ny_ts_status`, `ny_ts_utc` shared by models and tests |
| Generic test | `dbt/macros/test_nonnegative.sql` | — | custom `nonnegative` test (no dbt_utils dependency) |
| Source | `dbt/models/sources.yml` | — | `raw.trips` declared read-only |
| Staging | `dbt/models/staging/stg_trips.sql` | **view** | parses/classifies timestamps; does **not** duplicate the 1.8M-row trip table |
| Mart | `dbt/models/marts/fct_hourly_demand.sql` | **table** | UTC-hour spine + hourly aggregation |
| Schema tests | `stg_trips.yml`, `fct_hourly_demand.yml` | 23 tests | unique/not_null/accepted_values/nonnegative |
| Singular tests | `dbt/tests/*.sql` | 7 tests | DST fixture, timestamp accounting, round-trip, alignment, reconciliation, coverage guard |

Commands (also in the Makefile: `make dbt-run`, `make dbt-test`, `make dbt-build`, `make dbt-docs`):

```bash
export DBT_PROFILES_DIR="$PWD/dbt"
dbt run --project-dir dbt     # PASS=2 (view + table)
dbt test --project-dir dbt    # PASS=30, 0 failures
dbt docs generate --project-dir dbt
```

dbt `run` result: `staging.stg_trips` (view, 1,816,391 rows) and
`marts.fct_hourly_demand` (table, **725 rows**).
dbt `test` result: **30/30 PASS** (13 schema tests on the mart, 10 on staging,
7 singular tests).

---

## 2. Timestamp & DST handling

Citi Bike timestamps in this archive are **naive local America/New_York wall times**
stored as text (`YYYY-MM-DD HH:MM:SS.mmm`, 100% format match in the ingested data). The
loader's `started_at_utc` / `ended_at_utc` / `trip_duration_sec` columns are **all NULL**
in `raw.trips` (verified: 0 populated) — the dbt layer derives everything itself and
never reads those columns.

### Policy (implemented in `ny_timestamps.sql`)

1. **Explicit parse** (`ny_local_ts`): regex format check, month 1–12, day ≥ 1 and ≤
   days-in-month (leap-year aware), hour ≤ 23, minute ≤ 59, second ≤ 59 (rejects leap
   seconds). Only then is the text cast to `timestamp`. Anything else → status
   `'invalid'`. Pre-validation is mandatory: PostgreSQL's cast of impossible calendar
   dates throws, and ambiguous-time casts can throw on invalid dates.
2. **Status classification** (`ny_ts_status`) — PostgreSQL `AT TIME ZONE` semantics,
   verified empirically before implementation:
   - `valid`: round-trip through `America/New_York` is lossless;
   - `nonexistent`: round-trip ≠ input (spring-forward gap; PG maps gap locals forward,
     e.g. `2026-03-08 02:30` → `07:30Z`, round-trips as `03:30`);
   - `ambiguous`: local time maps back to itself from **both** −1h and +1h offsets
     (fall-back repeated hour; PG resolves ambiguous locals to the **later/standard**
     instant, e.g. `2025-11-02 01:30` → `06:30Z`);
   - `invalid`: parse failure (status `'invalid'`, UTC null).
3. **UTC derivation** (`ny_ts_utc`): `local at time zone 'America/New_York'`, applied
   **only** when status = `'valid'`. Ambiguous and nonexistent times are **never**
   assigned an invented UTC instant: `*_utc` stays NULL and such rows are excluded from
   UTC-based aggregation and counted separately in the reconciliation test.
4. **Duration**: `trip_duration_sec = extract(epoch from ended_at_utc − started_at_utc)`
   only when **both** timestamps are valid.

The policy is pinned by the synthetic fixture test `assert_dst_policy_fixture`
(19 cases: normal winter/summer, both edges of the 2025-11-02 fall-back, both sides and
the middle of the 2026-03-08 spring-forward, invalid calendar/format/leap-second/null),
mirroring the Phase 3 Python policy (flag ambiguity, reject gaps) but represented in SQL
so both layers stay consistent.

**Observed effect on this dataset:** January 2026 contains no DST transition, so
**all 1,816,391 trips classify as `valid` for both start and end**
(`select started_at_status, ended_at_status, count(*) … → 1,816,391 valid/valid`).
The DST machinery is fixture-verified and will engage on autumn/spring months.

### Coverage and calendar decisions (dbt vars)

| Var | Value | Rationale |
|---|---|---|
| `coverage_start` | `2026-01-01 00:00:00-05:00` | first covered local hour |
| `coverage_end` | `2026-02-01 00:00:00-05:00` | end of window (exclusive) |
| `coverage_gap_start` | `2026-01-25 12:00:00-05:00` | observed source outage, hour-aligned start |
| `coverage_gap_end` | `2026-01-26 07:00:00-05:00` | observed source outage, hour-aligned end |

- The published archive selects trips by **end** time within January → 266 December-start
  boundary trips exist (all ending ≥ Jan 1). They are excluded from the mart (before
  `coverage_start`) and accounted for in the reconciliation test.
- Source outage on 2026-01-25: last pre-gap trip started 12:03:29 local, first post-gap
  trip started 2026-01-26 07:03:17. The gap is conservatively hour-aligned to
  `[2026-01-25 12:00, 2026-01-26 07:00)` (19 hours), which also excludes the partially
  covered onset hour (4 trips). Hours inside the gap are **absent from the mart**, not
  zero-filled — coverage there is not verified. The coverage guard test fails if any
  covered hour lacks observed trips or any observed hour falls outside the spines
  (except the gap onset hour, which the guard explicitly allows).
- 744 − 19 = **725 covered hours**, one row each — matches the built table.

---

## 3. Data model

### `staging.stg_trips` (view)

One row per `ride_id`. Columns: source text preserved (`started_at_raw`,
`ended_at_raw`), parsed `*_local` timestamps, `*_utc` (valid-only), `*_status`,
`trip_duration_sec` (valid pairs only), station IDs as text with blanks normalized to
NULL **without dropping trips** (1,334 start / 8,799 end blanks), full geometry,
`member_casual` preserved unfiltered, `source_member`, `source_file_key`,
`_ingested_at`. Key: `ride_id` (unique + not_null tested).

### `marts.fct_hourly_demand` (table)

Grain: **exactly one row per UTC hour** (`hour_utc`) over the coverage spines
(UTC-hour spine is used because New York DST offsets are whole hours → strict 1:1
mapping between UTC hours and local hours, including across DST).

| Column | Description |
|---|---|
| `hour_utc` | PK; hour start as `timestamptz` (UTC) |
| `hour_local_start` | local wall-clock hour start |
| `hour_local_date`, `hour_local` | local calendar date, hour of day |
| `day_of_week_local`, `is_weekend_local` | local calendar attributes |
| `rides_total` / `rides_member` / `rides_casual` | trip starts in the hour (zero-filled within spines) |
| `is_source_truncated` | true for the final local day (see §6) |

Coverage/outage/boundary handling is entirely var-driven; re-verifying a new month means
updating the vars, not the SQL.

---

## 4. dbt tests

| Test | Type | Result |
|---|---|---|
| `unique`/`not_null` on `stg_trips.ride_id` | built-in | PASS |
| `not_null` on `stg_trips` raw start/end, statuses | built-in | PASS |
| `accepted_values` on both statuses | built-in | PASS |
| `nonnegative` on `trip_duration_sec` | custom generic | PASS |
| `unique`/`not_null` on `fct_hourly_demand.hour_utc` | built-in | PASS |
| `not_null`/`nonnegative` on all demand/calendar columns | built-in | PASS |
| `assert_dst_policy_fixture` | singular | PASS (19/19 cases) |
| `assert_valid_timestamp_conversion` | singular | PASS (0 invalid, 0 round-trip failures, UTC⇔valid bijection) |
| `assert_timestamp_accounting` | singular | PASS (statuses partition rows both ends; no negative durations) |
| `assert_utc_local_hour_alignment` | singular | PASS (hour, date, weekday, weekend all consistent with `hour_utc`) |
| `assert_member_casual_equals_total` | singular | PASS (member + casual = total in every hour) |
| `assert_reconciliation_eligible_to_hourly` | singular | PASS (see §5) |
| `assert_coverage_definition_matches_source` | singular | PASS (spines ⇄ observed hours) |

**`dbt test` total: PASS=30, WARN=0, ERROR=0.**

---

## 5. Real-data verification & reconciliation

### 5.1 Row-level accounting (`assert_reconciliation_eligible_to_hourly`)

- **Staged = raw:** 1,816,391 = 1,816,391 (view introduces no loss).
- **Valid-start partition:** valid = boundary-before + boundary-after + gap-window + Σ mart.
  Actual: 1,816,391 = 266 (Dec boundary) + 0 (after window) + 4 (gap onset hour) +
  1,816,121 (Σ `rides_total` in the mart). ✔
- **Spine = mart rows:** 725 = 725 (no missing/extra hours).
- **Per-hour counts:** full outer join of independently recomputed staging aggregates vs
  the mart → 0 mismatched hours (counts by member/casual/total).

Verification query (actual output):

```sql
select count(*) as mart_rows,
       min(hour_utc) as min_hour, max(hour_utc) as max_hour,
       sum(rides_total) as total_rides,
       sum(rides_member) as rides_member,
       sum(rides_casual) as rides_casual
from marts.fct_hourly_demand;
```

```
 mart_rows |        min_hour        |        max_hour        | total_rides | rides_member | rides_casual
-----------+------------------------+------------------------+-------------+--------------+-------------
       725 | 2026-01-01 05:00:00+00 | 2026-02-01 04:00:00+00 |     1816121 |      1653182 |       162939
```

(`min/max hour_utc` are the first/last covered hours: Jan 1 00:00 and Jan 31 23:00 local.)

### 5.2 Status distribution (actual)

```
 started_at_status | ended_at_status |  count
-------------------+-----------------+---------
 valid             | valid           | 1816391
```

### 5.3 Outage-window shape (actual)

```sql
select hour_local_date, sum(rides_total) as rides, count(*) as hours
from marts.fct_hourly_demand
where hour_local_date in ('2026-01-24','2026-01-25','2026-01-26')
group by 1 order by 1;
```

```
 hour_local_date | rides | hours
-----------------+-------+-------
 2026-01-24      | 41975 |    24
 2026-01-25      |  3049 |    12
 2026-01-26      |  6067 |    17
```

Jan 25 keeps only hours 00–11; Jan 26 resumes at hour 07 — the 19-hour gap is absent,
not zero-filled.

### 5.4 Peak-hour sanity (actual)

```sql
select hour_utc, hour_local_start, rides_total
from marts.fct_hourly_demand order by rides_total desc limit 3;
```

```
        hour_utc        | hour_local_start | rides_total
------------------------+------------------+-------------
 2026-01-14 13:00:00+00 | 2026-01-14 08:00 |       10811
 2026-01-08 22:00:00+00 | 2026-01-08 17:00 |       10791
 2026-01-13 22:00:00+00 | 2026-01-13 17:00 |       10551
```

Morning (08:00) and evening (17:00) commute peaks — consistent with Phase 3 findings.

### 5.5 Determinism

Two consecutive `dbt run` executions rebuilt both models. Content checksum:

```sql
select md5(string_agg(row(hour_utc, hour_local_start, rides_total,
                          rides_member, rides_casual)::text,
                      '|' order by hour_utc))
from marts.fct_hourly_demand;
```

Run 1: `fb3f6e4dcc6a4b71d4899d5984c4ebf2`
Run 2: `fb3f6e4dcc6a4b71d4899d5984c4ebf2` — **identical**.

---

## 6. Known limitations & edge cases

1. **Late-January volume collapse (source-side).** Daily totals fall from ~28k–107k
   (Jan 1–24) to ~16k–23k (Jan 27–31) with intact diurnal shape. Cause cannot be
   determined from the archive alone (possible partial source publication). The mart
   reports what the archive contains; no correction is applied. Phase 5 features/models
   must treat the last week as potentially unrepresentative.
2. **2026-01-25 source outage.** 19 hour-aligned hours are missing from the mart by
   design (see §2). Not zero-filled; `assert_coverage_definition_matches_source` pins
   this decision to the data.
3. **Right truncation of the final day.** Because the archive selects by end time,
   trips starting late on Jan 31 that end on Feb 1 are absent (≈169 midnight-crossing
   starts estimated from neighbouring days, plus starts after the last observed
   23:56:50). The final 24 hours carry `is_source_truncated = true`
   (24 hours, 18,360 rides in this run). Downstream consumers should avoid training on
   or scoring the final day without correction.
4. **266 December-start boundary trips** are excluded from the mart (before
   `coverage_start`) and explicitly reconciled.
5. **DST statuses unused this month.** All rows are `valid`; the ambiguous/nonexistent
   paths are proven only by the synthetic fixture. Months spanning March/November will
   exercise them for real.
6. **`stg_trips` is a view** — every dbt test re-parses `raw.trips` (full test run ≈ 13
   minutes locally). Accepted trade-off to avoid a second 1.8M-row trip table; revisit
   with incremental models if the dataset grows to multi-year scale.
7. **Weather/forecast layers untouched** (out of scope for 4A).

---

## 7. How to reproduce

```bash
make up                       # start PostgreSQL
make ingest                   # populate raw.trips (202601)
make dbt-run                  # staging view + hourly mart
make dbt-test                 # 30 tests
make dbt-docs                 # local docs site
```

The Python ingestion test suite is unaffected (`python3 -m pytest tests -v` —
15 passed, 13 skipped; skips are DB-gated integration tests), and
`raw.trips` was not modified by dbt (read-only source).
