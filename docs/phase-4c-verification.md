# Phase 4C Verification — Historical Data Expansion (Oct–Dec 2025)

**Date:** 2026-10-22. PostgreSQL 16 (Docker), dbt-core 1.12.6, dbt-postgres 1.11.0.
Baseline commit `d086a20` (tag `v0.4b`). All numbers below are actual outputs from the
live database and the independent reconciliation script — nothing is estimated.

This phase expands verified coverage from one month (January 2026) to four
(October–December 2025 + January 2026), generalizes the dbt transformation layer to be
window-driven, adds an explicit DST-ambiguity class, and optimizes staging. Forecasting,
weather, serving, MLflow, API, dashboard, and orchestration remain untouched.

---

## 1. Preflight (Step 1)

- **Git**: working tree clean at `d086a20`; tag `v0.4b` present. No commits made in 4C.
- **Disk**: 103 GiB free after expansion (was 116 GiB). `data/cache/` = 2.2 GB (4 archives).
- **PostgreSQL**: database `citi_bike` = 9,797 MB; `raw.trips` = 5,045 MB; `staging.stg_trips`
  = 4,743 MB.
- **S3 discovery**: `CitiBikeS3Listing.find_nyc_month()` was already month-generic and was
  used unchanged. All three target archives confirmed present:

  | Month | Archive key | Size (bytes) |
  |---|---|---|
  | 202510 | `202510-citibike-tripdata.zip` | 924,115,389 |
  | 202511 | `202511-citibike-tripdata.zip` | 667,113,932 |
  | 202512 | `202512-citibike-tripdata.zip` | 407,790,791 |

- **January-specific assumptions identified** before any change: the coverage window and
  the single 2026-01-25 outage were hardcoded dbt vars (`coverage_start/end/gap_start/gap_end`);
  the demand and coverage spines used a single-gap two-segment `generate_series`; the
  singular tests reconstructed the same single-gap spine; one test hardcoded `744` window
  hours; boundary/truncation logic assumed one window.
- **No January data was modified or deleted.** `raw.trips` only ever gained rows.

---

## 2. Ingestion (Step 2) — one month at a time

Each month was fetched from its cached archive, parsed member-by-member, and loaded with
`ride_id` uniqueness. Source counts were computed **independently** (stdlib CSV over the
cached ZIP) before comparing to the manifest.

| Month | CSV members | Source rows | Manifest `rows_read` | Canonical rows | Rejected | First-load wall time |
|---|---|---|---|---|---|---|
| 202510 | 5 | 4,731,900 | 4,731,900 | 4,731,900 | 0 | 2,633.6 s |
| 202511 | 4 | 3,417,052 | 3,417,052 | 3,417,052 | 0 | 2,130.0 s |
| 202512 | 3 | 2,091,620 | 2,091,620 | 2,091,620 | 0 | 1,414.0 s |
| 202601 (4B baseline) | 2 | 1,816,391 | 1,816,391 | 1,816,391 | 0 | (Phase 3) |

- **Provenance preserved**: `started_at_raw`/`ended_at_raw` verbatim; `source_member`,
  `source_file_key`, `_ingested_at` per row; `ingest.manifest` per archive with checksum,
  bytes, and row accounting.
- **Reconciliation** (Step 2.6): for every month `rows_read == rows_accepted + rows_rejected
  + duplicates_detected`, enforced by `complete_manifest()`; independent source count equals
  manifest read and canonical row count (see §4).
- **Checksums** (Step 2.7) recomputed from the cached ZIPs and compared to the manifest — all
  match (§4).
- **Idempotency** (Step 2.8): each month was re-run cleanly after load. Every rerun produced
  `rows_accepted = 0` and `duplicates_detected = <canonical rows>` (e.g. 202510 rerun: read
  4,731,900, accepted 0, dups 4,731,900). A mid-run interruption during the first October
  load (host process killed by a shell timeout) left only *committed* batches; the rerun
  inserted the remainder and correctly counted the already-present rows as duplicates,
  yielding the exact source total — durable, resume-safe batch accounting in action.
- **No truncation**: `raw.trips`, `raw.trips_rejects`, and `ingest.manifest` were only
  appended. `raw.trips_rejects` remains empty (0 rejects across all months).

---

## 3. dbt generalization (Step 3)

### 3.1 Window-driven coverage (replaces hardcoded January vars)

Coverage is now data, not code:

- **`dbt/seeds/coverage_windows.csv`** — one row per archive month with
  `coverage_start`/`coverage_end` as explicit-offset absolute instants (so the in-window
  DST transition is unambiguous) and a `window_label`.
- **`dbt/seeds/coverage_gaps.csv`** — verified outage windows; today exactly one row, the
  documented 2026-01-25 January outage, scoped to `month = 202601`.
- The hardcoded `coverage_*` vars were removed from `dbt_project.yml`; only `local_tz`
  remains a var.

The window seed, with the DST-correct offsets (`2025-11` ends `-05:00`, having crossed the
2025-11-02 fall-back):

| month | coverage_start (UTC) | coverage_end (UTC) |
|---|---|---|
| 202510 | 2025-10-01 04:00+00 | 2025-11-01 04:00+00 |
| 202511 | 2025-11-01 04:00+00 | 2025-12-01 05:00+00 |
| 202512 | 2025-12-01 05:00+00 | 2026-01-01 05:00+00 |
| 202601 | 2026-01-01 05:00+00 | 2026-02-01 05:00+00 |

### 3.2 New macro: `coverage_spine()`

`dbt/macros/coverage_spine.sql` emits, per window hour: `in_outage_window`,
`in_boundary_day`, `is_resume_hour`/`resume_boundary`, `onset_gap_start`, and a new
`is_dst_ambiguous_hour`. Every model and singular test now consumes this macro, so the
window logic has a single definition.

### 3.3 DST ambiguity — a new `uncertain` class

**Problem found by November:** on 2025-11-02 America/New_York the local wall clock hour
`01:00` repeats. Two UTC hours (05:00 and 06:00) render to that same local hour and — because
source timestamps there are ambiguous — cannot be attributed a verified trip count. In the
first multi-month build these hours appeared as `complete` with **0 demand**, which is
exactly the forbidden "treat an unverified missing hour as zero demand".

**Fix:** `coverage_spine()` flags an hour `is_dst_ambiguous_hour` when the local wall clock
fails to advance across it (fall-back repeat). `fct_hourly_coverage` classifies those hours
`coverage_status = 'uncertain'`, `coverage_reason = 'dst_ambiguous_hour'`;
`fct_hourly_demand` excludes them from the spine. `demand_rides_total` is **NULL** (never 0)
for uncertain hours. This is a general, DST-driven rule — not a hardcoded date range.

### 3.4 Status vocabulary (verified counts, all months)

| coverage_status | coverage_reason | hours | observed | demand |
|---|---|---|---|---|
| `complete` | — | 2,835 | 11,776,740 | 11,776,740 |
| `boundary` | `end_keyed_archive` | 96 | 275,324 | 275,324 |
| `partial` | `gap_onset` | 1 | 4 | *(NULL)* |
| `partial` | `gap_resume` | 1 | 132 | 132 |
| `unavailable` | `source_outage` | 18 | 0 | *(NULL)* |
| `uncertain` | `dst_ambiguous_hour` | 2 | 0 | *(NULL)* |
| **total** | | **2,953** | 12,052,200 | 12,052,196 |

`boundary` = last local day of each window (24 h × 4 windows = 96). No outage was invented
for Oct/Nov/Dec: they have zero `unavailable` hours. The only exclusions are the verified
January outage, the January gap onset/resume edge hours, and the two DST-ambiguous November
hours.

### 3.5 January behaviour preserved

The generalization is behaviour-preserving for January: the January **demand** slice is
byte-identical to the 4B baseline (see §5.4).

---

## 4. Independent reconciliation (Step 4)

`scripts/phase4c_reconcile.py` audits each cached archive with the stdlib CSV reader and
compares to the database — no ingestion or dbt imports.

### 4.1 Source records and checksums per archive

| Month | CSV members | Source rows | DB rows | Rows match | Checksum match | Bytes match | Manifest |
|---|---|---|---|---|---|---|---|
| 202510 | 5 | 4,731,900 | 4,731,900 | ✓ | ✓ | ✓ | completed |
| 202511 | 4 | 3,417,052 | 3,417,052 | ✓ | ✓ | ✓ | completed |
| 202512 | 3 | 2,091,620 | 2,091,620 | ✓ | ✓ | ✓ | completed |
| 202601 | 2 | 1,816,391 | 1,816,391 | ✓ | ✓ | ✓ | completed |

Checksums (SHA-256):

| Month | checksum_sha256 |
|---|---|
| 202510 | `6a8ea3a973ed92caa6129795ff5e0cc758aa9d42c81af579b8f1ebf9f555a6be` |
| 202511 | `279fc68f6ebc06dec938c7574f843707b2e86c590b3126050672938ad0a8345f` |
| 202512 | `504448a1cd0b8a4630da880a861bc73bbd4d88fad59a66f18ec23a84e5747f45` |
| 202601 | `8d579be9515f797739f0ff907576da44f25413bc677e1286a876696a71ae9755` |

### 4.2 Counts

- **Canonical records**: 12,056,963 (`raw.trips`).
- **Cross-archive duplicate `ride_id`s**: **0** (12,056,963 rows = 12,056,963 distinct
  `ride_id`). The archives are start-keyed and month-disjoint; boundary trips (e.g. a
  2025-10-31 start in the November archive) carry distinct IDs.
- **Staging**: 12,056,963 rows = 12,052,839 `valid` + 4,124 `ambiguous` (all in the
  2025-11-02 fall-back hour). Zero `nonexistent`/`invalid`.
- **Hourly demand**: 2,932 rows, 12,052,196 rides.
- **Coverage**: 2,953 rows (§3.4).

### 4.3 Missing / uncertain periods

- `unavailable` (verified outage): 18 hours — the 2026-01-25 source outage only.
- `partial`: 2 hours — January gap onset (4 trips, excluded) and gap resume (132 trips,
  retained).
- `uncertain`: 2 hours — the 2025-11-02 DST fall-back pair.
- **No hour anywhere is represented as zero demand unless trips were actually observed in
  it.** Uncertain absence is NULL, never 0.

### 4.4 Date-boundary handling

Valid starts outside the demand windows: **643**, fully accounted:

- **639** starts on 2025-09-30, i.e. before the 2025-10-01 window start (the start-keyed
  October archive selects some September-boundary trips). Correctly excluded; they belong to
  a September archive not ingested.
- **4** starts inside the January outage window (the gap onset hour).

643 + 12,052,196 demand rides = 12,052,839 valid starts. Exact identity.

Per-window right truncation: the last local day of each window (Oct 31, Nov 30, Dec 31,
Jan 31) is flagged `is_source_truncated` (24 hours each).

### 4.5 Local↔UTC DST checks

- `ambiguous` rows with a non-NULL UTC instant: **0** (conservative policy — no invented UTC).
- `valid` rows with a NULL UTC instant: **0**.
- Round-trip failures (`stg.started_at_utc at zone America/New_York <> started_at_local`):
  **0**.
- Confirmed: `2025-11-02 05:00+00 → 01:00 local`, `06:00+00 → 01:00 local`, `07:00+00 →
  02:00 local` (the repeated hour), handled by `assert_dst_fallback_coverage`.

---

## 5. dbt build (Step 5)

### 5.1 Full build

```
make up
export DBT_PROFILES_DIR="$PWD/dbt"
dbt build --project-dir dbt --full-refresh
```

Result: **PASS=120, WARN=0, ERROR=0, SKIP=0** — 2 seeds, 4 table models, 3 view models, and
111 data tests. Wall time **39 m 31 s**, dominated by the 2,484 s (≈41 min) `stg_trips`
rebuild.

### 5.2 Tests that assumed January-only data (fixed, not weakened)

| Test | Issue | Fix |
|---|---|---|
| `assert_coverage_status_rules` | hardcoded `744` window rows; single-gap bucket checks | expected rows now derived from the window seed; gap checks use `coverage_gaps` |
| `assert_coverage_definition_matches_source` | single-gap spine; every spine hour must have observed trips | spine from macro, DST-ambiguous hours excluded from the observed-trip requirement |
| `assert_reconciliation_eligible_to_hourly` | single-gap spine/window + partition | seed-driven window/gap; DST-ambiguous hours excluded from the spine count |
| `assert_daily_hourly_reconciliation` | `… <> 24` per day | compares to `expected_hours` (24, or 25 on the fall-back day) |
| `assert_station_starts_integrity` | implicitly assumed id→name 1:1 held | see §5.3 — model now resolves id→name |

New test added: **`assert_dst_fallback_coverage`** — asserts the 2025-11-02 repeated local
hour is exactly one hour with two UTC buckets, both `uncertain`/`dst_ambiguous_hour`, absent
from the demand mart, and that the day has 25 spine hours.

### 5.3 Data-quality finding surfaced by multi-month data

`assert_station_starts_integrity` failed on `station_id_to_name_not_unique` for station
**`4077.07`**: it is published as `Court St & Huntington St` (3,406 trips) except for 2
trips on 2025-11-03 published as `Court St & Nelson St`. This is a **source-side naming
inconsistency**, not a pipeline defect. Resolution: `mart_station_starts` now groups by
`start_station_id` (the dimension key) and resolves the canonical name deterministically
(most-frequent name per id, tie-broken by name), enforcing `id → name` 1:1 while every ride
is still counted. The integrity test (and the 1:1 rule) now passes; the stray rename is
documented here rather than silently dropped.

### 5.4 January reproducibility

The January **demand** slice (`hour_local_date` in 2026-01) hashes identically to the Phase 4B
baseline before and after the full-refresh rebuild:

```
md5(string_agg(row(hour_utc, rides_total, rides_member, rides_casual,
                   is_source_truncated)::text, '|' order by hour_utc))
-- 932239ddb15b700d184db56fa129ef90   (2026-01-01 .. 2026-01-31, 725 rows)
```

### 5.5 Performance bottleneck and optimization

The bottleneck was unambiguous: `stg_trips` was a **view**, so every downstream query
re-parsed and re-classified all ~12 M timestamps (a single test took ~90 s; the old
January-only 4B build took ~22 min for 1.8 M rows). With four months this was impractical.

**Optimization:** `stg_trips` is now materialized as a **table** with unique index on
`ride_id` and indexes on `started_at_utc` and `started_at_status`. Results are equivalent —
the same SQL, same rows (test `assert_reconciliation_eligible_to_hourly` proves
`staging_rows == raw_rows` and per-hour counts match).

Measured effect:

| Build | Wall time |
|---|---|
| 4B baseline (`stg_trips` view, January only, 1.8 M rows) | ≈ 22 min |
| 4C full-refresh (`--full-refresh`, 12.06 M rows, rebuilds `stg_trips`) | 39 m 31 s |
| 4C steady-state (`dbt build --exclude stg_trips`, 12.06 M rows, 105 tests) | **1 m 31 s** |

The one-time `stg_trips` rebuild is the cost; steady-state builds are ~15× faster than the
4B January-only build. A future incremental materialization on `_ingested_at` would remove
even that rebuild and is the natural next optimization (deferred).

---

## 6. Runtime and disk usage

- **Ingestion** (first load, wall): 202510 ≈ 43.9 min, 202511 ≈ 35.5 min, 202512 ≈ 23.6 min.
  Slower than January (11 min) because every insert checks uniqueness against the growing
  table; noted as a future optimization (bulk staging + `COPY` + set-based dedupe).
- **dbt**: full-refresh 39 m 31 s; steady-state 1 m 31 s.
- **Disk**: `data/cache/` 2.2 GB; database 9,797 MB (`raw.trips` 5,045 MB, `stg_trips`
  4,743 MB); host free space 103 GiB.

---

## 7. Known limitations

1. **Fall-back ambiguity is excluded, not imputed.** 4,124 trips in the 2025-11-02 repeated
   local hour cannot be placed in a UTC bucket; the hour is `uncertain` and its demand is
   NULL. Consumers needing per-hour completeness should treat `uncertain` as missing.
2. **Sept-30 boundary trips excluded** (639) — they belong to an uningested September
   archive; extending coverage backward would require ingesting 202509.
3. **Right-truncation at each window end** — the final local day of each archive is flagged
   `boundary`; late-evening trips that end after the window are absent from the source.
4. **Station `4077.07` source rename** resolved by majority-name; the 2 stray-name trips are
   reattributed to the canonical name (rides still counted).
5. **Ingestion throughput degrades with table size** — per-row `ON CONFLICT` inserts; a
   `COPY` + merge approach would help beyond ~20 M rows.
6. **`stg_trips` full rebuild ≈ 41 min** on 12 M rows; incremental materialization deferred.
7. **No `dim_stations`** yet (unchanged from 4B); station geography still deferred.

---

## 8. Readiness for Phase 5

- Coverage is now **window-driven and multi-month**; adding a month is a seed row plus an
  ingest run, not a code change.
- The demand mart is **leakage-safe by construction** and distinguishes complete / boundary /
  partial / unavailable / **uncertain**; no unverified hour is rendered as zero demand.
- **January remains byte-reproducible** (checksum `932239ddb15b700d184db56fa129ef90`).
- Training-hour selection for Phase 5 should use `fct_hourly_coverage.in_demand_mart`
  (complete + boundary + gap_resume), and treat `boundary` hours as truncated and
  `uncertain` hours as missing.
- Recommended before/with Phase 5: consider an **incremental `stg_trips`** so feature builds
  do not pay the 41-min rebuild.

**Phase 4C is complete. Awaiting review before Phase 5 (weather + leakage-safe features +
baseline forecasting).** No commits were made; the working tree holds all Phase 4C changes
uncommitted for your review.
