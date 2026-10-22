# Project Specification — Urban Mobility Analytics & Demand Forecasting

> Status: Phase 1 (discovery and specification). No application code, Docker files,
> migrations, or training scripts exist yet. Facts verified during Phase 1 are marked
> **[verified]**; assumptions and open questions are marked **[assumption]** / **[verify]**.

## 1. Problem statement

Bike-sharing systems are a form of public urban mobility whose operating cost is driven by
**rebalancing**: moving bikes between stations so that riders find a bike where demand is
high and a free dock where demand is low. Operators and city planners need to anticipate
demand in advance to schedule rebalancing crews, plan dock capacity, and evaluate
service levels. Poor anticipation causes empty stations (lost trips) and full stations
(riders unable to dock), both of which reduce service quality.

This project builds a reproducible, locally runnable system that ingests public
bike-sharing trip data, validates and transforms it into analytics-ready hourly demand,
trains a demand-forecasting model, and exposes forecasts through an API and a dashboard.

The project is deliberately scoped to demonstrate **data engineering, analytical SQL,
classical machine learning, orchestration, and MLOps fundamentals** — not to build a
production dispatch system.

### Target users (personas used to motivate requirements)

| Persona | Need |
|---|---|
| City mobility analyst | Understand demand patterns by hour, day, station, and weather |
| Bike-share operations planner | Anticipate per-station or system-wide demand to schedule rebalancing |
| Hiring manager / peer reviewer | Assess engineering quality, reproducibility, and evaluation rigor |
| The author (learning) | Practise a complete, defensible data + ML workflow |

## 2. Prediction target

**Primary target (finalized in Phase 2): hourly system-level trip-starts (citywide).**

- Target variable: `rides` = count of trips starting in a given hour (system-wide).
- Granularity: one row per **local hour** in `America/New_York` (the service's local
  timezone). All calendar features (hour/day/month/day-of-week/weekend/holiday) are
  derived from this local hour. Absolute time also stored as `hour_ts_utc` and/or
  `timestamptz` for joins/clarity. **[decided]**
- Prediction origin: **end of hour t** — the point at which all observations up to and
  including hour t are available (a "nowcast origin" for t+1).
- Horizon: **next hour (t+1)**. Forecast the total system-wide trip starts for hour t+1
  given information available at/through hour t. Multi-step and longer horizons are deferred. **[decided]**
- Evaluation: **chronological** train/validation/test splits by time. Use a gap ≥ horizon
  (at least 1h) between splits if there is any risk of leakage; splits are disjoint and
  cut on the time boundary, not by row number. **[decided]**
- Baseline: **seasonal-naive**. Primary candidate: `value at same hour previous day` (lag 24).
  Secondary: `value at same hour previous week` (lag 168). Also include a simple
  hour-of-week mean if helpful; document which baseline is the reference for the
  comparison. **[decided]**
- Metrics: **MAE** (primary), **RMSE** (secondary). Report per-segment (weekday/weekend,
  peak/off-peak) when relevant. No performance target is promised. **[decided]**
- Leakage prevention: each feature must be available at prediction origin (≤ t). 
  - Lag features (`rides_{t-1}`, `t-2`, `t-24`, `t-168`, etc.) use only past. 
  - Rolling statistics (means/sums over [3h,24h,168h]) are computed on the past (shift by 1,
    then rolling, closed left) and materialized with an explicit `feature_cutoff`. 
  - **Weather**: use only weather at hours ≤ prediction origin (lagged or current); never
    use forecast-hour weather. Because Open-Meteo timestamps in `America/New_York` must be
    joined carefully (DST offset bug; see caveat), join weather on **absolute time (UTC)**
    and/or `timeformat=unixtime`, then convert to NY local for alignment. **[decided]**
  - Missing hours: explicitly distinguish "no trips in hour" (true 0) from "hour not
    covered by ingestion" (missing → exclude or flag). **[decided]**

**Possible extension (deferred): per-station hourly demand.** This requires a station
dimension and increases sparsity/complexity. Deferred to keep the first iteration
tractable; the data model reserves room for it (§4 of `architecture.md`).

## 3. Inputs and outputs

### Inputs
- Raw public trip-level data from the selected bike-share system (see
  `dataset-evaluation.md`), supplied as periodic CSV archives.
- Hourly historical weather for the city (separate free source; see
  `dataset-evaluation.md`).
- Calendar features derived in-pipeline (hour, day-of-week, month, holiday flags).

### Outputs
- **Analytics layer:** validated, de-duplicated trip facts and an hourly demand table
  in PostgreSQL, exposed as dbt models.
- **Model artifacts:** trained model + evaluation metrics + preprocessing artifacts.
- **Experiment records:** MLflow runs (parameters, metrics, artifacts).
- **Serving layer (later weeks):** a FastAPI endpoint returning a demand forecast.
- **Presentation layer (later weeks):** a Streamlit dashboard (historical demand +
  forecast vs. actual).

## 4. Use cases

1. **Demand exploration:** query hourly/daily/weekly demand patterns and the effect of
   weather and calendar effects.
2. **Forecasting:** produce an hourly demand forecast for a future window with a stated
   accuracy estimate.
3. **Model comparison:** compare a naive/seasonal baseline against candidate ML models
   under a fixed, time-aware evaluation protocol.
4. **Reproducibility:** a new machine can reproduce the pipeline and the reported metrics
   from raw inputs using documented commands.

## 5. Analytics component vs. forecasting component

These are distinct and must not be conflated:

| Dimension | Analytics component | Forecasting component |
|---|---|---|
| Question | "What happened and why?" | "What will happen next?" |
| Direction | Descriptive / diagnostic | Predictive |
| Inputs | Full historical data | Features computed **only from the past** |
| Output | Tables, aggregates, dashboards | A numerical forecast + error estimate |
| Correctness concern | Clean, correct, well-modelled aggregates | **No leakage**, honest out-of-sample error |
| Primary tools | SQL, dbt, PostgreSQL, Streamlit | Python, scikit-learn, MLflow |
| Evaluation | Query correctness, grain/consistency tests | MAE/RMSE out-of-sample vs. baseline |

The analytics component produces the demand series that the forecasting component
consumes. They share a data model and a shared definition of "an hour of demand," but
they are documented, tested, and versioned as separate concerns.

## 6. Scope

### In scope (MVP)
- Idempotent ingestion of the selected dataset into a raw layer.
- Data validation and cleaning; aggregation to hourly demand.
- Weather enrichment from a free source.
- Relational storage in PostgreSQL with documented schema and constraints.
- SQL transformations and data tests (dbt Core) with a staging → marts layout.
- A naive/seasonal baseline and ≥1 candidate ML model, evaluated with a time-aware split.
- Experiment tracking (MLflow) and reproducible evaluation.
- CLI or Make targets to run each stage end to end.
- Tests, logging, configuration management, and CI checks.
- A forecasting API (FastAPI) and a lightweight dashboard (Streamlit).
- Docker Compose for local reproducibility.

### Out of scope (non-goals)
- Real-time streaming ingestion or true online serving.
- Production rebalancing optimization, routing, or fleet simulation.
- Per-station deep learning or GPU models.
- Microservices, Kubernetes, or cloud deployment.
- User accounts, authentication, or multi-tenant features.
- Paid data sources or APIs.
- Serving demand forecasts as an official operational tool.

## 7. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | System can ingest raw trip files for a configurable date range idempotently (re-running does not duplicate data). |
| FR-2 | System validates raw and transformed data against explicit rules and records failures/rejects. |
| FR-3 | System produces an analytics-ready hourly demand series joined with weather and calendar features. |
| FR-4 | System trains a baseline and ≥1 candidate model with a documented, time-aware evaluation protocol. |
| FR-5 | System logs parameters, metrics, and artifacts per experiment for reproducibility. |
| FR-6 | System exposes a forecast via an API and displays history + forecast in a dashboard. |
| FR-7 | A new environment can reproduce the pipeline and metrics from documented commands. |

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Reproducibility: pinned dependencies; fixed random seeds; versioned data/model artifacts. |
| NFR-2 | Idempotency: ingestion and transformations are safe to re-run. |
| NFR-3 | Observability: structured logging and clear error messages; failures are diagnosable. |
| NFR-4 | Testability: unit tests for transforms/features; integration tests for ingestion + dbt models. |
| NFR-5 | Maintainability: modular layout, type hints where practical, formatter + linter enforced in CI. |
| NFR-6 | Transparency: no invented metrics; every reported number is traceable to a run. |

## 9. High-level success criteria (definition of done for the project)

- End-to-end run from raw data to forecast completes with documented commands.
- A baseline and ≥1 ML model are compared on a held-out, time-ordered test window using
  MAE and RMSE, with the comparison reproducible from MLflow.
- dbt tests and application tests pass in CI.
- API returns a forecast; dashboard shows history and forecast.
- `README` + docs let a reviewer run the system without asking the author.

> No performance target is stated in advance. The project claims only that the ML
> candidate will be **compared fairly** against the baseline; whether it beats the
> baseline is an empirical result to be reported honestly, including negative results.

## 10. Open questions (to resolve before or during Phase 2)

1. Timezone handling: report and model in local time or UTC? (Affects "hour" semantics.)
2. Demand definition: trip *starts* per hour only, or net flow (starts minus ends) per
   station? Starts-first is proposed for simplicity.
3. Data window: how many months to ingest to balance seasonality coverage against
   download/processing size.
4. Weather join granularity: single city point vs. multiple grid points.
5. Whether to model holidays from a library or a static config.
6. Station-level forecasting: keep deferred, or promote if time allows.

## 11. Risks (summary — detail in `implementation-roadmap.md`)

- Dataset scope creep / large downloads.
- Timezone and DST errors corrupting the hourly grain.
- Feature leakage from future information (lags, rolling windows, weather availability).
- Overinvestment in dashboard/orchestration before the pipeline and evaluation are sound.

## 12. Traceability

- Dataset choice and evidence: `dataset-evaluation.md`.
- Components, data flow, Mermaid diagram, data model: `architecture.md`.
- Weekly plan, acceptance criteria, risks, deferrals: `implementation-roadmap.md`.
- Project summary and links: `../README.md`.
