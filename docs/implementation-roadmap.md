# Implementation Roadmap — Six Weeks

> Phase 1 output. Sequence is intentional: a working data pipeline and a defensible
> forecasting evaluation come **before** dashboard polish and orchestration. Each week has
> objectives, deliverables, dependencies, acceptance criteria, and risks/fallbacks.
> No performance targets are promised in advance; results will be reported honestly.

## Guiding priorities (in order)

1. Correct data pipeline (ingest → validate → transform → store).
2. A **defensible, leakage-free** forecasting evaluation vs. a baseline.
3. Reproducibility, tests, and CI.
4. Serving (API) and dashboard.
5. Orchestration (Airflow) — last, optional.

An item in a later tier never delays an item in an earlier tier.

---

## Milestone 0 — Verification (Phase 2, done) 
**Status:** ✓ complete. S3 XML listing verified, 202601 sample inspected (schema, quality),
weather DST caveat identified, window sizes quantified. Decisions recorded in this doc set.

## Week 1 — Dataset lock, environment, and raw ingestion

**Objectives**
- Finalize **window + dev slice** (default: 1 month dev, 6–12 months MVP). 
- Stand up reproducible environment. 
- Month-by-month ingestion (stream) from S3 listing; landing in Parquet per month;
  manifest/checksums.

**Deliverables**
- `docker-compose.yml` → PostgreSQL (MLflow added later). 
- Skeleton: `src/` (ingest, transform, features, models), `dbt/`, `tests/`, `Makefile`,
  `pyproject.toml`, `.env.example`, `.gitignore` (ignore `data/`).
- Source adapter parsing S3 listing; variable filenames, skip non-CSV; idempotent.
- Raw landing → Parquet + (optionally) `raw.trips` in Postgres for the configured window.

**Acceptance criteria**
- `make ingest WINDOW=202601` downloads/lands 202601; re-run produces no duplicates.
- Landing Parquet exists; manifest written; `data/` untracked.
- Compliance note documented (no raw data in repo).

**Risks & fallbacks**
- Volume: shrink window or switch to JC for CI. 
- Filename inconsistency: parse listing, don't hardcode. 
- Anomalous months (202602, 202604): flag if selected. 
- Nested zips avoided by using 2024+ monthly.

---

## Week 2 — Validation, cleaning, and hourly aggregation

**Objectives**
- Conform trips; build `dim_stations`, `fct_trips`, `fct_hourly_demand` (bucketed by NY local hour).
- Treat end-station nulls and duration outliers correctly.

**Deliverables**
- Validation rules reflecting sample: handle null end stations, <60s and >24h trips
  (either flag or filter per policy, never silently drop). 
- dbt: `stg_trips`, `stg_stations`, `dim_stations`, `fct_trips`, `fct_hourly_demand`.
- dbt tests (grain, not-null, coverage). 
- Hour-with-no-trips materialized as 0; distinguish "no trips" vs "coverage gap".

**Dependencies:** Week 1.

**Acceptance criteria**
- `make dbt-run && make dbt-test` passes.
- `fct_hourly_demand` gapless over covered window (where ingestion covered). 
- Integrity checks pass. Sanity check of weekday profile.

**Risks & fallbacks**
- Timezone/DST: bucket by NY local; add DST boundary tests. 
- Volume: keep trips in Parquet and load only marts to Postgres if slow. 
- Station ID suffixes/alphanumeric (JC): treat as text.

---

## Week 3 — Weather enrichment + feature engineering (leakage-safe)

**Objectives**
- Join weather (UTC-aligned, to avoid DST offset bug); build feature table with leakage guard.

**Deliverables**
- Weather module: Open-Meteo archive, request UTC (`timezone=GMT` or unixtime), convert to
  NY local; hourly → `raw.weather_hourly`, `stg_weather`.
- `fct_hourly_demand_enriched` (demand + weather + calendar from NY local hour).
- Feature builder: `ml.features_hourly` with `feature_cutoff`; lags, shift-then-rolling;
  use only weather ≤ prediction origin.
- Chronological split (train/val/test), fixed cutoffs, seeds.

**Dependencies:** Week 2 marts.

**Acceptance criteria**
- Weather join coverage documented; missing weather flagged, not zeroed silently.
- Leakage tests pass: no feature after target timestamp. 
- Split reproducible.

**Risks & fallbacks**
- Confirm Open-Meteo terms. If blocked, ship calendar-only features; add weather later.
- Leakage from rolling windows: shift(1) then rolling, `closed='left'`. 
- Grid cell selected; document returned coords.

---

## Week 4 — Baseline, candidate models, and honest evaluation

**Objectives**
- Baseline vs candidate, chronological evaluation.

**Deliverables**
- Baseline: **lag-24 (seasonal-naive, same hour previous day)** primary; lag-168 and
  hour-of-week mean secondary.
- ≥1 candidate (start with regularised linear; `HistGradientBoosting` or LightGBM CPU optional).
- Metrics: **MAE** (primary), **RMSE**. Segment reports.
- MLflow tracking added (params/metrics/artifacts). `ml.predictions`, `ml.model_registry`.

**Dependencies:** Week 3.

**Acceptance criteria**
- Metrics reproducible from MLflow run ID.
- Same time-ordered test window for baseline and candidate.
- Comparison states result honestly (beat/match/underperform).

**Risks & fallbacks**
- Underperforming vs baseline is acceptable and reportable. 
- Keep complexity modest. Freeze metric set pre-run.

---

## Week 5 — Serving (API) + tests + CI

**Objectives**
- Serve model and harden. Add later only after core works (can run in parallel lightly, but
  priority: pipeline solid).

**Deliverables**
- FastAPI app with `/forecast` (predictions + model version) and `/health`. `make serve`.
- Model loading from MLflow/artifact.
- Unit tests for transforms/features (esp. leakage: shift-then-rolling, cutoff). Integration
  test for ingest + dbt (dev slice).
- GitHub Actions CI: lint (ruff), format check, typecheck (mypy), pytest.

**Dependencies:** Week 4.

**Acceptance criteria**
- CI green on push. `/forecast` returns well-formed response (local, Docker Compose).
- New clone → documented commands reproduce tests and a forecast.

**Risks & fallbacks**
- DB-dependent tests: service container or ephemeral test DB; mark slow tests. Keep API
  read-only.

---

## Week 6 — Dashboard, documentation, orchestration (optional), and demo

**Objectives**
- Present results and finalize docs. Keep Make as orchestrator by default.

**Deliverables**
- Streamlit dashboard (history, actual vs forecast, filters) — if time. 
- `README` finalized: quickstart (dev slice + window), architecture, results (MLflow run ID),
  limitations (volume, DST, weather terms).
- Demo material. 
- If desired, Airflow DAG only if time allows; otherwise document `Makefile` as
  orchestrator (do not block MVP).

**Dependencies:** Weeks 2–5.

**Acceptance criteria**
- Reviewer can run documented commands on a clean machine and reproduce headline metric
  (MLflow run ID traceable).
- Deferred items explicitly listed. 
- Documentation accurate (window sizes, git-ignore, DST caveat, license notes).

**Risks & fallbacks**
- Dashboard/orchestration never block core pipeline/evaluation. Airflow out of MVP. 
- Time overrun: freeze features; use 6-month window or dev slice.

---

## Deferred features (explicit)

- Per-station forecasting (keep station dim, don't model yet).
- Multi-step probabilistic forecasts / prediction intervals.
- Additional systems (JC, Divvy, Capital Bikeshare) as multi-city generalisation.
- GBFS near-real-time ingestion/online updates.
- Cloud deployment, autoscaling, monitoring/alerting.
- Deep-learning models (GPU) — out of scope by policy.
- Pre-2024 nested yearly-zips processing (focus on 2024+ monthly).

## Cross-week risks register (summary)

| Risk | Severity | Mitigation / fallback |
|---|---|---|
| Feature leakage (lags/rolling/weather) | High | `feature_cutoff` guard; shift-then-rolling tests; use only up-to-origin weather |
| Timezone/DST errors | High | Bucket by NY local; weather joined on UTC (to avoid fixed offset bug); DST tests |
| Data volume / download failures | High | Month-by-month streaming; dev slice 1 month; window 6–12m; JC/UCI fallback |
| Ingestion gotchas (naming, quotes, nested zips, multi-file) | Medium | Parse S3 listing; skip `__MACOSX`; 2024+ monthly only |
| Licence misunderstanding | Medium | No raw data committed; document fetch; confirm Open-Meteo terms |
| Airflow overrun | Low | Make default; Airflow optional/future |
| Dashboard priority inversion | Low | Core pipeline/evaluation first; docs priority |
| Dishonest/overstated results | High | Metrics frozen pre-run; MLflow run IDs required |

## Definition of done (project)

Documented commands reproduce, on a clean machine: the pipeline, the dbt tests, the training
and evaluation, and a served forecast — with every headline number traceable to a tracked run.
