<img src="docs/rwcheck_logo.png" alt="RWCheck" width="300">

> **RWCheck is a CLI and REST API for Fast Retraction Screening of DOIs, PubMed IDs, and BibTeX References**

**Check DOIs and PubMed IDs. .bib files against the [Retraction Watch](https://retractionwatch.com/) dataset.**
`rwcheck` ingests the Retraction Watch Data into a local **SQLite** database for O(log n) indexed lookups, exposes a **FastAPI REST API**, and provides a **CLI** for interactive and batch queries — all with no external database services required.

## Features

- **SQLite-backed** — fast indexed lookup; no Postgres/Redis required.
- **Python API** (`rwcheck`) — import and call directly from Python; no server needed.
- **REST API** (`rw_api/`) — OpenAPI docs, rate limiting, 5-min cache, daily auto-update.
- **CLI** (`rwcheck`) — single DOI/PMID, batch from file, offline or API mode.
- **Automatic updates** — API rebuilds the DB every 24 h; CLI `update` command pulls and hashes the latest CSV.
- **Reproducible** — every response includes dataset version (SHA-256), row count, and build timestamp.


## Quickstart

### 1. Install

```bash
# Clone and install in editable mode (Python 3.10+)
git clone https://github.com/khan-lab/rwcheck.git
cd rwcheck
pip install -e ".[dev]"
```

### 2. Build the local database

**From the local CSV** (if you already downloaded `retraction_watch.csv`):

```bash
make build-db
# or explicitly:
python scripts/build_db.py --csv retraction_watch.csv --db data/rw.sqlite
```

**Download the latest CSV from GitLab and build:**

```bash
make build-db-online
# or:
python scripts/build_db.py --url
```

The build takes ~20 s on a modern laptop for ~69 k rows.

### 3. Check a DOI

```bash
rwcheck doi 10.1038/nature12345
rwcheck doi "https://doi.org/10.1038/nature12345"   # URL prefix is stripped
```

### 4. Check a PubMed ID

```bash
rwcheck pmid 12345678
```

### 5. Batch check from a file

**Plain text** (one DOI per line):

```bash
rwcheck batch-doi papers.txt
rwcheck batch-doi papers.txt --out tsv > results.tsv
rwcheck batch-doi papers.txt --out json | jq '.results[] | select(.matched)'
```

**CSV file** (specify column with `--col`):

```bash
rwcheck batch-doi references.csv --col doi
```

### 6. Check a BibTeX file

```bash
rwcheck batch-bib refs.bib
```

This parses every entry in the `.bib` file, extracts DOIs (from the `doi` field, or a `url` field containing `doi.org`), and PubMed IDs (from `pmid`, or `eprint`+`eprinttype=pubmed`), then queries them all against the local database.

Two report files are written next to the input file:

| File | Contents |
|---|---|
| `refs_rwcheck.md` | Human-readable Markdown: summary table, retracted entries with details, clean list |
| `refs_rwcheck.json` | Machine-readable JSON: full match data, suitable for further processing |
| `refs_rwcheck.html` | Self-contained HTML report: styled, browser-viewable, collapsible retracted entries |

```bash
# Write reports to a specific directory
rwcheck batch-bib refs.bib --report-dir ./reports/

# Use the remote API instead of the local DB
rwcheck batch-bib refs.bib --api http://localhost:8000
```

**Example output (stdout):**

```
  Total references          42
  Retracted                  3
  Clean (not found)         37
  Unchecked (no DOI/PMID)    2

⚠ Retracted entries:
  ✗ [smith2020] Smith et al. 2020 — Retraction | Nature

Reports written:
  Markdown → refs_rwcheck.md
  JSON     → refs_rwcheck.json
```

### 7. Update the database

```bash
rwcheck update           # downloads latest CSV; skips if unchanged
rwcheck update --force   # force rebuild regardless
```

## REST API

### Start the server

```bash
make api
# → http://127.0.0.1:8000
# Docs: http://127.0.0.1:8000/docs
```

The server automatically downloads the latest Retraction Watch CSV on startup and every 24 hours thereafter.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/meta` | Dataset metadata (version, row count, build time) |
| `GET` | `/stats` | Aggregate statistics (totals, by year, top journals, by country) |
| `GET` | `/check/doi/{doi}` | Look up a DOI (slashes in DOIs are supported) |
| `GET` | `/check/pmid/{pmid}` | Look up a PubMed ID |
| `POST` | `/check/batch` | Batch lookup (up to 500 items) |
| `POST` | `/check/bib` | Upload a `.bib` file; returns retracted/clean summary |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

### Examples

```bash
# Dataset info
curl http://localhost:8000/meta

# DOI lookup
curl "http://localhost:8000/check/doi/10.1038/nature12345"

# PubMed ID lookup
curl "http://localhost:8000/check/pmid/12345678"

# Batch
curl -X POST http://localhost:8000/check/batch \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/nature12345", "10.9999/test"], "pmids": [12345678]}'
```

### Response format

```json
{
  "query": "10.1038/nature12345",
  "matched": true,
  "matches": [
    {
      "record_id": 42,
      "title": "Example retracted paper",
      "journal": "Nature",
      "retraction_nature": "Retraction",
      "reason": "Falsification/Fabrication of Data;",
      "retraction_date": "2022-03-15",
      "original_paper_doi": "10.1038/nature12345",
      "retraction_doi": "10.1038/nature12345retract",
      "original_paper_pmid": 12345678
    }
  ],
  "meta": {
    "dataset_version": "a1b2c3d4e5f6a7b8",
    "built_at": "2024-11-01T12:00:00+00:00",
    "row_count": "68999",
    "source_url": "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
  }
}
```


## Python API

Use `rwcheck` directly from Python without starting the HTTP server.

```python
from rwcheck import check_doi, check_pmid, check_batch

# Single DOI lookup — returns dict
result = check_doi("10.1038/nature12345", db_path="data/rw.sqlite")
if result["matched"]:
    m = result["matches"][0]
    print(m["retraction_nature"], m["retraction_date"])

# Single PMID lookup — returns dict
result = check_pmid(12345678, db_path="data/rw.sqlite")

# Batch lookup — returns JSON string
import json
raw = check_batch(
    dois=["10.1038/nature12345", "10.9999/test"],
    pmids=[12345678],
    db_path="data/rw.sqlite",
)
data = json.loads(raw)
retracted = [r for r in data["results"] if r["matched"]]
```

If the `RW_DB_PATH` environment variable is set, `db_path` can be omitted:

```python
import os, rwcheck
os.environ["RW_DB_PATH"] = "data/rw.sqlite"

result = rwcheck.check_doi("10.1038/nature12345")
```

### Return shapes

| Function | Returns | Keys |
|---|---|---|
| `check_doi(doi)` | `dict` | `query`, `matched`, `matches`, `meta` |
| `check_pmid(pmid)` | `dict` | `query`, `matched`, `matches`, `meta` |
| `check_batch(dois, pmids)` | `str` (JSON) | `results` (list), `meta` |

Each item in `matches` / `results[].matches` contains: `record_id`, `title`, `journal`, `retraction_nature`, `retraction_date`, `reason`, `original_paper_doi`, `retraction_doi`, `original_paper_pmid`, `country`, `paywalled`, and more.


## Docker

```bash
# Build image
make docker-build

# Run (mounts ./data for persistent SQLite DB)
make docker-run
```

Or directly:

```bash
docker build -t rwcheck .
docker run -p 8000:8000 -v "$(pwd)/data:/app/data" rwcheck
```


## CLI Reference

```
Usage: rwcheck [OPTIONS] COMMAND [ARGS]...

  Check DOIs/PMIDs against the Retraction Watch dataset.

Commands:
  doi         Check a single DOI.
  pmid        Check a single PubMed ID.
  batch-doi   Batch-check DOIs from a text or CSV file.
  batch-pmid  Batch-check PMIDs from a text or CSV file.
  batch-bib   Check all references in a BibTeX file; write JSON + Markdown report.
  update      Download the latest dataset and rebuild the local DB.

Options:
  --version   Show version and exit.
  --help      Show this message and exit.
```

### Common options

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to local SQLite DB (default: `data/rw.sqlite`) |
| `--api URL` | Use remote API instead of local DB |
| `--json` | Output raw JSON (single-item commands) |
| `--out json\|tsv\|table` | Output format for batch commands |
| `--col NAME` | CSV column name for batch commands |
| `--report-dir DIR` | Directory for `batch-bib` report files |
| `--force` | Force DB rebuild even if unchanged |


## Environment variables (API + Python API)

| Variable | Default | Description |
|----------|---------|-------------|
| `RW_DB_PATH` | `data/rw.sqlite` | SQLite database path (used by API server and Python API) |
| `RW_CSV_URL` | GitLab raw URL | Retraction Watch CSV source |
| `RATE_LIMIT` | `60/minute` | slowapi rate limit per IP |
| `UPDATE_INTERVAL_HOURS` | `24` | Hours between auto-updates |


## Development

```bash
make install    # pip install -e ".[dev]"
make test       # pytest
make lint       # ruff + mypy
make fmt        # ruff format + fix
make test-cov   # pytest with coverage report
```


## Data source

The Retraction Watch dataset is maintained by the [Center for Scientific Integrity](https://retractionwatch.com/) and distributed via [CrossRef on GitLab](https://gitlab.com/crossref/retraction-watch-data). Please review their [terms of use](https://gitlab.com/crossref/retraction-watch-data) before deploying publicly.

