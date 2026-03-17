# Installation

## Requirements

- Python 3.10 or later
- A writable home directory (the CLI database is stored at `~/.rwcheck/rw.sqlite`)

---

## Install from PyPI

```bash
pip install rwcheck
```

This installs the `rwcheck` CLI, the Python library, and all runtime dependencies.

---

## Install from source

```bash
git clone https://github.com/khan-lab/rwcheck.git
cd rwcheck
pip install -e ".[dev]"
```

The `[dev]` extra adds `pytest`, `ruff`, and `mypy`.

---

## Build the local database

RWCheck queries a local SQLite file. Build it once after installation:

```bash
# Download the latest CSV from GitLab and build the DB
rwcheck update
```

The database is written to `~/.rwcheck/rw.sqlite` by default (the directory is created automatically). Override with `--db` or the `RW_DB_PATH` environment variable:

```bash
rwcheck update --db /opt/rwcheck/rw.sqlite
# or
RW_DB_PATH=/opt/rwcheck/rw.sqlite rwcheck update
```

To rebuild from a local CSV file:

```bash
build-rw-db --csv retraction_watch.csv
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RW_DB_PATH` | `~/.rwcheck/rw.sqlite` (CLI) / `data/rw.sqlite` (API/Docker) | Path to the SQLite database |
| `RW_CSV_URL` | GitLab URL | Source URL for `rwcheck update` |
| `RATE_LIMIT` | `60/minute` | REST API rate limit (e.g. `120/minute`) |
| `UPDATE_INTERVAL_HOURS` | `24` | Auto-update interval for the REST API |

---

## Docker

Run the REST API in a container with a persistent volume for the database:

```bash
docker build -t rwcheck:latest .

docker run -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  rwcheck:latest
```

On first start the container automatically downloads and builds the database. The browser UI is available at [http://localhost:8000](http://localhost:8000) and the OpenAPI docs at [http://localhost:8000/docs](http://localhost:8000/docs).

### Environment overrides

```bash
docker run -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  -e RATE_LIMIT=120/minute \
  -e UPDATE_INTERVAL_HOURS=12 \
  rwcheck:latest
```

---

## Docs extras

To build or serve this documentation locally:

```bash
pip install "rwcheck[docs]"
mkdocs serve          # live preview at http://127.0.0.1:8000
mkdocs build          # build static site to site/
```

Or use the Makefile shortcuts:

```bash
make docs-serve
make docs
```
