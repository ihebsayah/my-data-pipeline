.PHONY: help up down build test lint typecheck format clean ingest dbt-run dbt-test dbt-build dbt-docs

export DBT_PROFILES_DIR := $(CURDIR)/dbt

help:
	@echo "Available commands:"
	@echo "  make up          Start PostgreSQL"
	@echo "  make down        Stop PostgreSQL"
	@echo "  make build       Install dependencies"
	@echo "  make test        Run tests"
	@echo "  make lint        Run linter"
	@echo "  make typecheck   Run type checker"
	@echo "  make format      Format code"
	@echo "  make clean       Clean cache files"
	@echo "  make ingest      Ingest default month (202601)"
	@echo "  make dbt-run     Run dbt staging + mart models"
	@echo "  make dbt-test    Run dbt tests"
	@echo "  make dbt-build   Run dbt models and tests"
	@echo "  make dbt-docs    Generate dbt documentation"

up:
	docker-compose up -d

down:
	docker-compose down

build:
	pip install -e ".[dev]"

test:
	pytest tests -v

lint:
	ruff check src tests

typecheck:
	mypy src

format:
	ruff format src tests

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov dbt/target dbt/logs

ingest:
	citi-ingest --month 202601

dbt-run:
	dbt run --project-dir dbt

dbt-test:
	dbt test --project-dir dbt

dbt-build:
	dbt build --project-dir dbt

dbt-docs:
	dbt docs generate --project-dir dbt
