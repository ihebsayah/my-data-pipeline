# Phase 4B Verification — Coverage, Quality, and Descriptive Analytics Layer

**Date:** 2026-01 (verification run); dbt-core 1.12.6, dbt-postgres 1.11.0, PostgreSQL 16 (Docker).
Full `dbt build`: **PASS=106, ERROR=0, SKIP=0** (7 models + 99 data tests), runtime ≈ 22 min
(staging view re-parses the 1.8M-row source on every run).

This phase is a Senior Analytics Engineer review and extension of Phase 4A: re-audit the
audited facts against the live database, then add an explicit **hourly coverage model** and
four **descriptive analytics models** on top of `fct_hourly_demand`, with quality tests.
All numbers below are actual outputs from the live database (queries shown). Forecasting,
weather, serving, and orchestration remain untouched.

---

## 1. Implementation summary

| Component | Path | Materialization | Grain / rows |
|---|---|---|---|
| Coverage spine | `dbt/models/marts/fct_hourly_coverage.sql` | **table** | one row per hour of the full window (744 rows) |
| Daily rollup | `dbt/models/marts/mart_daily_demand.sql` | view | one row per local date (31 rows) |
| Hourly pattern | `dbt/models/marts/mart_demand_pattern_hourly.sql` | view | hour × daytype (48 rows) |
| Rider mix | `dbt/models/marts/mart_rider_mix.sql` | view | daytype (2 rows) |
| Station starts | `dbt/models/marts/mart_station_starts.sql` | **table** | station id × name, incl. NULL bucket (2,208 rows) |
| Schema tests/docs | `dbt/models/marts/analytics_schema.yml` | — | descriptions + 87 schema tests on the new models |
| Singular tests | `dbt/tests/assert_{coverage_status_rules,coverage_demand_consistency,daily_hourly_reconciliation,station_starts_integrity,rider_pattern_consistency}.sql` | — | 5 new invariants |

Test taxonomy (`dbt ls --resource-type test`): 78 marts schema tests, 9 staging schema
tests, 12 singular tests (7 from 4A + 5 new) = **99 data tests**, all passing.

```bash
export DBT_PROFILES_DIR="$PWD/dbt"
dbt build --project-dir dbt    # 3 table models + 4 view models + 99 tests, PASS=106
```

Lineage: `raw.trips` → `staging.stg_trips` → `fct_hourly_demand` → `fct_hourly_coverage`
→ {`mart_daily_demand`, `mart_demand_pattern_hourly`, `mart_rider_mix`} and
`stg_trips` → `mart_station_starts`. Tables were chosen for coverage and station models
(each test otherwise re-scans 1.8M staged trips); the three small descriptive models stay
views over the tables.

---

## 2. Phase 4A audit — re-verification and refinements

Every 4A headline figure was re-verified against the live database before building on it.

### 2.1 Verified without change

| Claim | Verification |
|---|---|
| raw = staged = distinct `ride_id` = 1,816,391 | `count(*)`, `count(distinct ride_id)` agree across raw and staging |
| 266 December-boundary starts (Dec 30: 1, Dec 31: 265) | all end in January; max end `2026-01-02 00:43:54` |
| Mart = 725 hours, 1,816,121 rides = 1,816,391 − 266 − 4 | exact identity holds |
| Interior gap has 0 trips; gap onset hour has exactly 4 | last pre-gap start `11:59:54.494`, first post-gap `07:03:17.101`, no start in `(12:03:30, 07:03:17)` |
| 0 zero-ride hours in the mart | `count(*) filter (where rides_total = 0)` = 0 |
| Member 1,653,182 / casual 162,939 in the eligible set | re-summed from the mart |

### 2.2 Resume hour refined: partial, but retained in the mart

The first post-gap trip starts **07:03:17**, i.e. ~3.3 minutes *inside* the resume hour.
At the resume hour's observed rate (132 trips/h ≈ 2.2/min), the probability of observing
zero trips in a fully available 3.3-minute window is e^(−7.2) ≈ 0.0007 — the outage
almost certainly extended ~3 minutes into the hour. The resume hour is therefore classified
**`partial`** (reason `gap_resume`), **retained in the demand mart** (its 132 rides are real
observed trips), and flagged. Expected undercount ≤ ~7 rides.

This is deliberately asymmetric with the onset hour (excluded entirely: only 4 of ~30
expected trips observed) — the classification is driven by how much of each hour is
actually observed, not by symmetry of definition.

### 2.3 Right-truncation estimate sharpened

Measured evening crossers (trips starting ≥ 21:00 local that end the next day):

| Start date | 21:00 | 22:00 | 23:00 | total |
|---|---|---|---|---|
| 2026-01-29 | — | 2 | 54 | 56 |
| 2026-01-30 | 7 | 8 | 57 | **72** |

Last observed start `2026-01-31 23:56:50.315`. Projecting the final evening onto the
missing Feb 1 boundary: **≈72–89 trips (~0.4–0.5% of Jan 31) are absent from the archive
entirely** — they are not in any mart and cannot be recovered from this source. The
`is_source_truncated` flag (24 boundary hours, 18,360 rides) is kept exactly as 4A defined
it; this estimate documents its magnitude precisely.

### 2.4 Station integrity

- Eligible trips (window-bounded, gap-excluded): 1,816,121.
- Distinct non-null start stations: **2,207**; station id → name is **1:1** (0 conflicts).
- **1,334** eligible trips have NULL start station — id and name are blank together
  (single NULL group), 0.07% of rides.
- Trip duration (eligible): p50 450s, p99 2,692s, p99.9 12,103s, max 89,998s (~25h) —
  no impossible values, no test added (duration is not used by any 4B model).

### 2.5 Late-January volume collapse — unchanged

Daily totals drop from ~28k–107k (Jan 1–24) to ~16k–23k (Jan 27–31) with intact diurnal
shape. Cause is not determinable from the archive alone; documented as a limitation, not
explained away.

---

## 3. The coverage model — `fct_hourly_coverage`

Design rule: **the demand mart is a subset of verified coverage; the coverage model is the
superset of the window including hours the demand mart refuses to assert.**

### 3.1 Structure

1. A **UTC hour spine** over the full var-driven window (744 hours) — hours are never
   dropped, even outage hours.
2. `observed_trips`: the literal count of eligible trips per hour *from the source*,
   regardless of the gap window (onset hour = 4; unavailable hours = 0). This is the
   "what actually exists in the archive" counter.
3. LEFT JOIN `fct_hourly_demand` → `demand_rides_total`, `in_demand_mart`. Never
   zero-filled: `demand_rides_total` is **NULL** (not 0) for hours not in the mart.

### 3.2 Status classification (verified counts)

| `coverage_status` | `coverage_reason` | hours | observed | demand | in mart |
|---|---|---|---|---|---|
| `complete` | NULL | 700 | 1,797,629 | 1,797,629 | yes |
| `boundary` | `end_keyed_archive` | 24 | 18,360 | 18,360 | yes |
| `partial` | `gap_onset` | 1 | 4 | NULL | **no** |
| `partial` | `gap_resume` | 1 | 132 | 132 | **yes** |
| `unavailable` | `source_outage` | 18 | 0 | NULL | no |

- `complete`: hour in window, not in the gap, not the final truncated day.
- `boundary`: final local day of an end-keyed archive (right truncation); in the mart but
  always flagged.
- `unavailable`: hour-aligned gap hours with zero observed trips → demand must be NULL.
- `partial`: an hour where coverage is *uncertain* — gap onset (mostly missing, excluded)
  or gap resume (first trip late, retained). `partial` is not a synonym for "excluded";
  the `in_demand_mart` column is the authoritative "should this hour be treated as real
  demand" flag.
- Invariants (pinned by `assert_coverage_status_rules`): `in_demand_mart` ⟺
  (complete or boundary or gap_resume); demand NULL ⟺ not in mart; unavailable ⟹
  observed = 0 and not in mart; boundary ⟺ `is_source_truncated`; resume hour has a late
  first trip; onset hour lies inside the gap window.

### 3.3 Verification evidence

```sql
select coverage_status, coverage_reason, count(*) hours,
       sum(observed_trips) observed, sum(demand_rides_total) demand
from marts.fct_hourly_coverage group by 1,2 order by 1,2;
```

```
 coverage_status |  coverage_reason  | hours | observed |  demand
-----------------+-------------------+-------+----------+----------
 boundary        | end_keyed_archive |    24 |    18360 |    18360
 complete        |                   |   700 |  1797629 |  1797629
 partial         | gap_onset         |     1 |        4 |
 partial         | gap_resume        |     1 |      132 |      132
 unavailable     | source_outage     |    18 |        0 |
```

744 hours total; 725 in the demand mart (identical to 4A's mart by construction);
19 excluded (18 unavailable + onset). Exactly **1** hour has observed trips but NULL
demand (the onset hour) — the "never zero-fill uncertain coverage" rule is observable in
the data. Determinism checksum (single rebuild of this phase; 4A established determinism
of the underlying demand mart `fb3f6e4dcc6a4b71d4899d5984c4ebf2`):

```sql
select md5(string_agg(row(hour_utc, coverage_status, coverage_reason,
                          observed_trips, demand_rides_total)::text,
                      '|' order by hour_utc))
from marts.fct_hourly_coverage;
-- 5539c78ff2d0d80f41514c7ad9210f2b
```

---

## 4. Data quality results

Full `dbt build`: **106/106 PASS** (0 failures, 0 warnings, 0 skips).

The five new singular tests assert cross-model invariants, not just column hygiene:

| Test | Invariant |
|---|---|
| `assert_coverage_status_rules` | status/reason/mart-membership/NULL rules of §3.2, plus 744-row spine |
| `assert_coverage_demand_consistency` | every demand hour exists in coverage with identical values and flags; sums equal; no demand hour is flagged unavailable; observed ≥ demand everywhere in-mart |
| `assert_daily_hourly_reconciliation` | daily rollup sums equal the hourly mart exactly (rides, member, casual, observed); per-day status-hour counts sum to 24 |
| `assert_station_starts_integrity` | station sums equal eligible trips (1,816,121); zero duplicate id-name pairs; NULL bucket present exactly once; no station attributed rides outside the eligible set |
| `assert_rider_pattern_consistency` | pattern and mix model totals equal the demand mart; 48/2 expected rows; member+casual shares sum to 1 per daytype |

Schema-level tests (in `analytics_schema.yml`) cover PK uniqueness, not-null on all
non-nullable keys, `accepted_values` for status/mix/rideable columns, nonnegative counts,
`share ≤ 1`, and full column descriptions (no code comments by project convention).

---

## 5. Descriptive findings

All figures from the live marts, covering only the verified-demand hours (725 hours,
1,816,121 rides).

### 5.1 Daily demand

- Busiest day: **2026-01-08 (Thursday), 106,612 rides**; also Jan 14 (104,851), Jan 13
  (100,729), Jan 9 (100,717), Jan 7 (97,830) — the top-5 are all weekdays.
- Weakest covered days are the outage-affected Jan 25 (3,049 rides over 12 covered hours)
  and Jan 26 (6,067 over 17) and the truncated Jan 31 (18,360 over 24 flagged hours).
- Coverage annotations per day (`mart_daily_demand`): Jan 25 = 12 covered / 1 partial /
  11 unavailable; Jan 26 = 17 covered (incl. the partial resume hour) / 7 unavailable;
  Jan 31 = 24 boundary hours. Only days whose every hour is `complete` are marked
  `is_fully_covered_day`; the boundary day is deliberately excluded from that label.
- `trips_excluded_from_demand` sums to 4 for the month — the 4 onset-hour trips observed
  but withheld from the demand mart.

### 5.2 Hourly pattern (weekday vs weekend)

| | Weekday | Weekend |
|---|---|---|
| Covered hours | 521 | 204 |
| Rides | 1,448,473 | 367,648 |
| Member share | **91.7%** | **88.3%** |

- Weekday peaks: **17:00 (avg 6,402 rides/h), 08:00 (5,870), 18:00 (5,671)** — classic
  commute shape; the 08:00 hour has the month's highest member share (**94.6%**).
- Weekend peak: **12:00 (3,831 rides/h)** with a broad midday plateau 12:00–14:00
  (3,831 / 3,501 / 3,378) — leisure shape, morning commute hour is the weekday's weakest
  relative hour.
- Weekend hours are fewer by construction: Jan 2026 has 9 weekend days (216 hours), minus
  12 outage-lost hours on Sunday Jan 25 = 204; weekday 528 − 7 = 521; 204 + 521 = 725 ✓.

### 5.3 Rider mix

Weekday: 1,328,386 member / 120,087 casual. Weekend: 324,796 member / 42,852 casual.
The system is membership-dominated year-round in this archive; casual share rises on
weekends (+3.4 pp), consistent with leisure use.

### 5.4 Station starts

Top start stations (all in-mart eligible trips; 1,816,121 total):

| Rank | Station | Rides | Share |
|---|---|---|---|
| 1 | W 21 St & 6 Ave | 7,554 | 0.416% |
| 2 | Pier 61 at Chelsea Piers | 6,939 | 0.382% |
| 3 | Lafayette St & E 8 St | 6,236 | 0.343% |
| 4 | W 31 St & 7 Ave | 6,174 | 0.340% |
| 5 | 9 Ave & W 33 St | 6,085 | 0.335% |
| 6 | Broadway & E 14 St | 5,598 | 0.308% |
| 7 | 11 Ave & W 41 St | 5,596 | 0.308% |
| 8 | E 33 St & 1 Ave | 5,467 | 0.301% |
| 9 | E 17 St & Broadway | 5,291 | 0.291% |
| 10 | 1 Ave & E 68 St | 5,207 | 0.287% |

Demand is highly fragmented: the top station holds just 0.42% of rides across 2,207
stations. Station-level forecasting will need grouping or regularization. The **NULL
station bucket (1,334 rides, 0.07%)** is retained explicitly rather than dropped, so
reconciliation stays exact.

---

## 6. Known limitations

1. **Resume hour may undercount ≤ ~7 rides** (§2.2) — retained in the mart, flagged
   `partial`. Consumers needing strict completeness should filter to `complete`+`boundary`
   via `fct_hourly_coverage`.
2. **≈72–89 trips missing from Jan 31** (§2.3) — inherent to an end-keyed archive; no
   imputation attempted. `boundary` hours are usable but biased low.
3. **Late-January volume collapse** (§2.5) — source-side, undeterminable; training on
   Jan 27–31 without segmentation risks learning a level shift that may be an artifact.
4. **Coverage is Jan-2026-specific** — vars pin this window; extending the archive
   requires updating `coverage_start/end` and re-validating the gap vars against the new
   data before trusting `partial`/`unavailable` classifications.
5. **`observed_trips` and `demand_rides_total` agree everywhere in-mart in this run** —
   the consistency test will catch future divergence (e.g. a filter change in the demand
   mart), but today it passes trivially for value equality.
6. **Runtime** — full build ≈ 22 min, dominated by re-parsing the 1.8M-row staging view
   (4A trade-off, unchanged). Incremental staging is the obvious future optimization.
7. **No station dimension yet** — `mart_station_starts` works from the flat trip table;
   the planned `dim_stations` (architecture §6.3) with lat/lon would enable geographic
   aggregation and is deferred, not forgotten.

---

## 7. Recommendations for the forecasting phase

1. **Training set** = hours where `fct_hourly_coverage.in_demand_mart` is true; treat
   `boundary` hours as truncated (drop or down-weight) and consider dropping the
   `gap_resume` hour for strict completeness.
2. **Never zero-fill** the 19 excluded hours — the coverage model exists precisely so
   downstream features can join on it and represent absence explicitly.
3. **Segment or down-weight Jan 27–31** until the volume collapse is understood.
4. **Rider mix is a stable descriptive baseline**: weekday ~92% member, weekend ~88%;
   the 08:00 hour at 94.6% member is the strongest commuter signature for validating
   feature pipelines.
5. **Station features**: use top-N stations + "other"/NULL buckets; a single station's
   max share is 0.42%, so per-station models would be extremely sparse at hourly grain.

---

## 8. How to reproduce

```bash
make up                       # start PostgreSQL
make ingest                   # populate raw.trips (202601), if empty
make dbt-build                # all 7 models + 99 tests (≈ 22 min)
make dbt-docs                 # local docs site
```

Python ingestion tests are unaffected (`python3 -m pytest tests -v` — 15 passed,
13 DB-gated skips). `raw.trips` was not modified (read-only source; all 4B models are
pure SQL transformations of the staging view).

---

**Phase 4B is complete and awaiting approval before Phase 5 (features + baseline
forecasting) is planned.**
