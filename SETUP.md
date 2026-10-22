# Setup Guide

## Prerequisites
- Python 3.11+
- Docker and Docker Compose
- ~10GB free disk space for full month (cached)

## Quick Start

### 1. Install dependencies
```bash
make build
# or
pip install -e ".[dev]"
```

### 2. Configure environment
```bash
cp .env.example .env
```

### 3. Start PostgreSQL
```bash
make up
# or
docker-compose up -d
```

Wait for PostgreSQL to be ready:
```bash
docker-compose ps
```

### 4. Ingest data (default: January 2026)
```bash
make ingest
# or
citi-ingest --month 202601
```

### 5. Run dbt transformations
```bash
make dbt-run     # staging.stg_trips (view) + marts.fct_hourly_demand (table)
make dbt-test    # 30 dbt tests
make dbt-docs    # generate local docs site
```

dbt credentials come from `POSTGRES_*` environment variables (see `.env.example`); the
Makefile exports `DBT_PROFILES_DIR` to the project's `dbt/profiles.yml` automatically.

### 6. Run tests
```bash
make test
```

## Commands

- `make up` - Start PostgreSQL
- `make down` - Stop PostgreSQL
- `make build` - Install dependencies
- `make test` - Run tests
- `make lint` - Run linter
- `make typecheck` - Run type checker
- `make format` - Format code
- `make ingest` - Ingest default month
- `make dbt-run` - Run dbt models (staging + marts)
- `make dbt-test` - Run dbt tests
- `make dbt-build` - Run dbt models and tests
- `make dbt-docs` - Generate dbt documentation
- `make clean` - Clean cache files

## CLI Usage

```bash
citi-ingest --month 202601           # Ingest January 2026
citi-ingest --month 202601 --dry-run # Dry run
```

## Database Schema

Tables created automatically:
- `ingest.manifest` - Ingestion tracking with checksums
- `raw.trips` - Landing records (raw values preserved)
- `raw.trips_rejects` - Rejected records with reasons

Created by dbt (`make dbt-run`):
- `staging.stg_trips` - View: parsed/classified trips with DST status + UTC timestamps
- `marts.fct_hourly_demand` - Table: one row per covered UTC hour of trip starts

## Notes

- Data is cached in `data/cache/`
- Raw data is never committed to git
- Timestamps are treated as America/New_York local time
- Duplicate ride_ids within same file/member are rejected
- Safe to re-run; duplicates prevented by constraints
- Only 1 month downloaded by default
