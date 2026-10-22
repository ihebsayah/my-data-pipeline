# Urban Mobility Analytics & Demand Forecasting

A reproducible, locally runnable data platform that ingests public bike-sharing trip data,
validates and transforms it into analytics-ready hourly demand, trains a demand-forecasting
model, and exposes forecasts through an API and a lightweight dashboard.

This is a portfolio and learning project focused on **data engineering, analytical SQL,
classical machine learning, pipeline orchestration, and MLOps fundamentals**. It is
CPU-only, free/open-source, and designed for a single developer.

> **Status: Phase 4B complete — coverage, quality, and descriptive analytics layer
> implemented and verified.** Raw ingestion (Phase 3) loads January 2026 Citi Bike trips
> into PostgreSQL. The dbt project (`dbt/`) now produces `staging.stg_trips`, the
> demand/coverage marts (`marts.fct_hourly_demand`, `marts.fct_hourly_coverage`), and
> four descriptive marts (`mart_daily_demand`, `mart_demand_pattern_hourly`,
> `mart_rider_mix`, `mart_station_starts`) — 99 dbt tests, all passing. See
> `docs/phase-4a-verification.md` and `docs/phase-4b-verification.md` for evidence.
> Forecasting, weather enrichment, serving, and orchestration are not started.

## Intended stack

| Layer | Tool | Tier |
|---|---|---|
| Ingestion / processing | Python + Pandas | Essential |
| Storage | PostgreSQL | Essential |
| Transformations + data tests | dbt Core | Essential |
| Forecasting | scikit-learn | Essential |
| Local reproducibility | Docker Compose | Essential |
| Quality (tests, config, CI) | pytest, pydantic-settings, Make, GitHub Actions | Essential |
| Experiment tracking | MLflow | Useful |
| Serving | FastAPI | Useful |
| Dashboard | Streamlit | Useful |
| Orchestration | Apache Airflow | Optional (added last) |

See `docs/architecture.md` for component responsibilities, data flow, a Mermaid diagram, and
the proposed data model.

## Plan (six weeks)

1. Dataset lock, environment, raw ingestion
2. Validation, cleaning, hourly aggregation
3. Weather enrichment + leakage-safe features
4. Baseline + candidate models with honest evaluation
5. Serving (API), tests, CI
6. Dashboard, documentation, optional orchestration, demo

Full detail, acceptance criteria, and risks: `docs/implementation-roadmap.md`.

## Documentation

- [Project specification](docs/project-specification.md) — problem, scope, users, use cases,
  requirements, non-goals, analytics vs. forecasting.
- [Dataset evaluation](docs/dataset-evaluation.md) — candidate comparison, source links,
  verification status, recommendation.
- [Architecture](docs/architecture.md) — components, data flow, Mermaid diagram, data model.
- [Implementation roadmap](docs/implementation-roadmap.md) — six-week plan, acceptance
  criteria, risks, deferred features.
- [Phase 3 verification](docs/phase-3-verification.md) — ingestion layer evidence.
- [Phase 4A verification](docs/phase-4a-verification.md) — dbt layer evidence: DST policy,
  coverage decisions, reconciliation, determinism.
- [Phase 4B verification](docs/phase-4b-verification.md) — coverage model, data quality
  suite, descriptive analytics findings, forecasting recommendations.

## Data, licensing, and compliance

- Primary source: **Citi Bike NYC trip data** (monthly zips, 2024+). Its data-sharing
  policy permits non-commercial analysis but **prohibits redistributing the data as a
  stand-alone dataset** — raw data is **never committed** (fetched programmatically,
  `data/` landing is git-ignored).
- Weather enrichment: **Open-Meteo Historical API** (`/v1/archive`). **Important:** When
  requesting `timezone=America/New_York`, Open-Meteo currently returns a fixed offset
  resolved at request time (DST changes ignored) → join weather on **UTC** (e.g.,
  `timezone=GMT` or `timeformat=unixtime`) to avoid misalignment. Confirm licence terms
  before publishing derived weather data.
- Every claim is labelled verified/unverified/inferred/assumed (see `dataset-evaluation.md`).
  No dataset properties, licences, or metrics are invented. Attribution given where required.

## Getting started

```bash
make up                       # start PostgreSQL
cp .env.example .env          # configure credentials
make ingest                   # ingest January 2026 into raw.trips
make dbt-run                  # staging + demand, coverage, and descriptive marts
make dbt-test                 # 99 dbt tests
make dbt-docs                 # local dbt docs site
make test                     # Python ingestion test suite
```
