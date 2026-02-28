.PHONY: build-db build-db-online api test lint fmt docker-build docker-build-api docker-run compose-up compose-down compose-logs compose-build docs docs-serve clean help

PYTHON  ?= python3
DB_PATH ?= data/rw.sqlite
CSV_PATH ?= retraction_watch.csv
RW_CSV_URL ?= https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Database ──────────────────────────────────────────────────────────────────

build-db:  ## Build DB from local CSV (default: retraction_watch.csv)
	$(PYTHON) scripts/build_db.py --csv $(CSV_PATH) --db $(DB_PATH)

build-db-online:  ## Download latest CSV from GitLab and (re)build DB
	$(PYTHON) scripts/build_db.py --url $(RW_CSV_URL) --db $(DB_PATH)

build-db-force:  ## Force rebuild from local CSV even if unchanged
	$(PYTHON) scripts/build_db.py --csv $(CSV_PATH) --db $(DB_PATH) --force

# ── API ───────────────────────────────────────────────────────────────────────

api:  ## Start the FastAPI server (development mode with auto-reload)
	uvicorn rw_api.main:app --reload --reload-exclude data --host 127.0.0.1 --port 8000

api-prod:  ## Start the FastAPI server (production mode)
	uvicorn rw_api.main:app --host 0.0.0.0 --port 8000 --workers 2

# ── Tests & lint ──────────────────────────────────────────────────────────────

test:  ## Run the test suite
	pytest -v

test-cov:  ## Run tests with coverage report
	pytest -v --cov=rwcheck --cov=rw_api --cov=scripts --cov-report=term-missing

lint:  ## Run ruff linter + mypy type checker
	ruff check .
	mypy rwcheck rw_api scripts --ignore-missing-imports

fmt:  ## Auto-format code with ruff
	ruff format .
	ruff check --fix .

# ── Docker ────────────────────────────────────────────────────────────────────

docker-build:  ## Build the CLI image (docker/cli/Dockerfile)
	docker build -f docker/cli/Dockerfile -t rwcheck:latest .

docker-build-api:  ## Build the API image (docker/api/Dockerfile)
	docker build -f docker/api/Dockerfile -t rwcheck-api:latest .

docker-run:  ## Run the API in Docker (mounts ./data for persistent DB)
	docker run --rm -p 8000:8000 \
		-v "$(PWD)/data:/app/data" \
		-e RW_CSV_URL=$(RW_CSV_URL) \
		rwcheck-api:latest

# ── Docker Compose (production) ───────────────────────────────────────────────

compose-build:  ## Build the rwcheck image via Compose
	docker compose build

compose-up:  ## Start Traefik + rwcheck in the background (requires .env)
	docker compose up -d

compose-down:  ## Stop and remove containers (volumes are preserved)
	docker compose down

compose-logs:  ## Tail logs from all services
	docker compose logs -f

# ── Housekeeping ──────────────────────────────────────────────────────────────

clean:  ## Remove build artefacts and cached files
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

install:  ## Install the package in editable mode (with dev dependencies)
	pip install -e ".[dev]"

# ── Docs ──────────────────────────────────────────────────────────────────────

docs:  ## Build the MkDocs documentation site (output: site/)
	mkdocs build

docs-serve:  ## Serve docs locally with live reload (http://127.0.0.1:8000)
	mkdocs serve
