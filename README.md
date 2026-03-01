<img src="docs/rwcheck_logo.png" alt="RWCheck" width="300">

> **RWCheck — Fast retraction screening for DOIs, PubMed IDs, and BibTeX references**

Check DOIs, PubMed IDs, and `.bib` files against the [Retraction Watch](https://retractionwatch.com/) dataset.
`rwcheck` ingests the Retraction Watch data into a local **SQLite** database for O(log n) lookups, exposes a **FastAPI REST API**, and provides a **CLI** for interactive and batch queries — no external database required.

**Live API:** <https://rwcheck.khanlab.bio>

---

## Features

- **REST API** — OpenAPI docs, rate limiting, 5-min cache, daily auto-update.
- **CLI** — single DOI/PMID/title lookup, batch from file, BibTeX screening.
- **SQLite-backed** — fast indexed lookup; no Postgres or Redis required.
- **Python API** — import and call directly; no server needed.
- **Auto-updates** — API rebuilds the DB every 24 h; CLI `update` command pulls and verifies the latest CSV.
- **Reproducible** — every response includes dataset version (SHA-256), row count, and build timestamp.

## Quickstart

### 1. Install

```bash
git clone https://github.com/khan-lab/rwcheck.git
cd rwcheck
pip install -e ".[dev]"   # Python 3.10+
```

### 2. Build the local database

```bash
make build-db-online      # download latest CSV from GitLab and build (~20 s, ~69 k rows)
```

Or from a CSV you already have:

```bash
make build-db             # uses retraction_watch.csv in the current directory
```

### 3. Check a DOI

```bash
rwcheck doi 10.1038/nature12345
rwcheck doi "https://doi.org/10.1038/nature12345"   # URL prefix is stripped
```

### 4. Check a PubMed ID

```bash
rwcheck pmid 12345678
```

### 5. Check by title

```bash
rwcheck title "Moderation of gut microbiota is associated with..."
```

> **Note:** Matching is exact and case-insensitive. When a match is found a warning
> is shown — confirm the result with a DOI or PMID before citing.

### 6. Batch check from a file

```bash
# Plain text (one DOI per line)
rwcheck batch-doi papers.txt
rwcheck batch-doi papers.txt --out tsv > results.tsv
rwcheck batch-doi papers.txt --out json | jq '.results[] | select(.matched)'

# CSV file (specify column with --col)
rwcheck batch-doi references.csv --col doi
```

### 7. Check a BibTeX file

```bash
rwcheck batch-bib refs.bib
```

Extracts DOIs and PubMed IDs from every entry and queries them against the local database.
Three report files are written next to the input:

| File | Contents |
|---|---|
| `refs_rwcheck.md` | Human-readable Markdown: summary table + retracted entries |
| `refs_rwcheck.json` | Machine-readable JSON: full match data |
| `refs_rwcheck.html` | Self-contained HTML report: styled, collapsible retracted entries |

```bash
# Write reports to a specific directory
rwcheck batch-bib refs.bib --report-dir ./reports/

# Use the live API instead of the local DB
rwcheck batch-bib refs.bib --api https://rwcheck.khanlab.bio
```

### 8. Update the database

```bash
rwcheck update           # download latest CSV; skip if unchanged
rwcheck update --force   # force rebuild regardless
```

## REST API

A public instance is running at **<https://rwcheck.khanlab.bio>**.
Interactive docs (Swagger UI) are available at <https://rwcheck.khanlab.bio/docs>.

### Run locally

```bash
make api
# → http://127.0.0.1:8000
# Docs: http://127.0.0.1:8000/docs
```

The server downloads the latest Retraction Watch CSV on startup and every 24 hours thereafter.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Landing page with live DOI checker and dataset stats |
| `GET` | `/meta` | Dataset metadata (version, row count, build time) |
| `GET` | `/stats` | Aggregate statistics (totals, by year, top journals, by country) |
| `GET` | `/check/doi/{doi}` | Look up a DOI (slashes in DOIs are supported) |
| `GET` | `/check/pmid/{pmid}` | Look up a PubMed ID |
| `GET` | `/check/title/{title}` | Exact title lookup (case-insensitive); result includes `match_type: "title"` warning |
| `POST` | `/check/batch` | Batch lookup (up to 500 items) |
| `POST` | `/check/bib` | Upload a `.bib` file; returns retracted/clean summary |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

### Examples

```bash
# Dataset metadata
curl https://rwcheck.khanlab.bio/meta

# DOI lookup
curl "https://rwcheck.khanlab.bio/check/doi/10.1038/nature12345"

# PubMed ID lookup
curl "https://rwcheck.khanlab.bio/check/pmid/12345678"

# Title lookup (URL-encode spaces and special characters)
curl "https://rwcheck.khanlab.bio/check/title/Moderation%20of%20gut%20microbiota"

# Batch lookup
curl -X POST https://rwcheck.khanlab.bio/check/batch \
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

# Single DOI lookup
result = check_doi("10.1038/nature12345", db_path="data/rw.sqlite")
if result["matched"]:
    m = result["matches"][0]
    print(m["retraction_nature"], m["retraction_date"])

# Single PMID lookup
result = check_pmid(12345678, db_path="data/rw.sqlite")

# Batch lookup
import json
raw = check_batch(
    dois=["10.1038/nature12345", "10.9999/test"],
    pmids=[12345678],
    db_path="data/rw.sqlite",
)
retracted = [r for r in json.loads(raw)["results"] if r["matched"]]
```

Set `RW_DB_PATH` to omit `db_path` in every call:

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

Each item in `matches` contains: `record_id`, `title`, `journal`, `retraction_nature`, `retraction_date`, `reason`, `original_paper_doi`, `retraction_doi`, `original_paper_pmid`, `country`, `paywalled`, and more.


## Docker

Docker images are published to the GitHub Container Registry:

| Image | Description |
|---|---|
| `ghcr.io/khan-lab/rwcheck` | CLI tool |
| `ghcr.io/khan-lab/rwcheck-api` | REST API |

### CLI image

```bash
# Pull and run
docker run --rm -v "$(pwd)/data:/app/data" ghcr.io/khan-lab/rwcheck doi 10.1038/nature12345

# Build locally
make docker-build
docker run --rm -v "$(pwd)/data:/app/data" rwcheck:latest doi 10.1038/nature12345
```

### API image

```bash
# Pull and run
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" ghcr.io/khan-lab/rwcheck-api

# Build locally
make docker-build-api
make docker-run       # equivalent: docker run -p 8000:8000 -v ./data:/app/data rwcheck-api:latest
```

### Production deployment (Docker Compose + Caddy)

See [DEPLOY.md](DEPLOY.md) for full EC2 setup instructions with Caddy reverse proxy, automatic HTTPS, and persistent volumes.


## CLI Reference

```
Usage: rwcheck [OPTIONS] COMMAND [ARGS]...

  Check DOIs, PMIDs, and titles against the Retraction Watch dataset.

Commands:
  doi         Check a single DOI.
  pmid        Check a single PubMed ID.
  title       Check by exact title (case-insensitive); warns to confirm with DOI/PMID.
  batch-doi   Batch-check DOIs from a text or CSV file.
  batch-pmid  Batch-check PMIDs from a text or CSV file.
  batch-bib   Check all references in a BibTeX file; write JSON/Markdown/HTML reports.
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


## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RW_DB_PATH` | `data/rw.sqlite` | SQLite database path |
| `RW_CSV_URL` | GitLab raw URL | Retraction Watch CSV source |
| `RATE_LIMIT` | `60/minute` | API rate limit per IP (slowapi) |
| `UPDATE_INTERVAL_HOURS` | `24` | Hours between automatic DB updates |
| `PUBLIC_HOST` | `http://localhost:8000` | Base URL shown in API responses and landing page |


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
