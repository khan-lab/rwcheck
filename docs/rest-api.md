# REST API

The rwcheck REST API is a self-hosted [FastAPI](https://fastapi.tiangolo.com/) service that exposes all lookup functionality over HTTP. It includes a browser UI, OpenAPI docs, rate limiting, and a background auto-update scheduler.

## Starting the server

=== "Local (development)"
    ```bash
    pip install rwcheck
    rwcheck update           # build the DB first
    make api                 # or: uvicorn rw_api.main:app --reload
    ```

=== "Docker"
    ```bash
    docker run -p 8000:8000 -v "$PWD/data:/app/data" rwcheck:latest
    ```

The browser UI is at `http://localhost:8000` and the OpenAPI (Swagger) docs are at `http://localhost:8000/docs`.

---

## Endpoints

### `GET /health`

Liveness check. Returns `200 OK` if the service is running.

**Response**
```json
{ "status": "ok", "db_exists": true }
```

---

### `GET /meta`

Dataset provenance — when the DB was last built and from where.

**Response**
```json
{
  "dataset_version": "abc123def4567890",
  "built_at": "2024-06-01T12:00:00",
  "row_count": "45231",
  "source_url": "https://gitlab.com/crossref/retraction-watch-data/...",
  "csv_sha256": "abc123..."
}
```

---

### `GET /stats`

Aggregate statistics about the dataset.

**Response**
```json
{
  "total_records": 45231,
  "total_journals": 8412,
  "total_countries": 94,
  "doi_coverage": 39801,
  "pmid_coverage": 32100,
  "by_year": [["2000", 12], ["2001", 34], ...],
  "top_journals": [["PLOS ONE", 2100], ...],
  "by_country": [["United States", 12000], ...],
  "meta": { ... }
}
```

---

### `GET /check/doi/{doi}`

Check a single DOI.

**Path parameter**

| Parameter | Description |
|---|---|
| `doi` | URL-encoded DOI (e.g. `10.1038%2Fnature12345`) |

**Example**
```bash
curl "http://localhost:8000/check/doi/10.1038%2Fnature12345"
```

**Response**
```json
{
  "query": "10.1038/nature12345",
  "matched": false,
  "matches": [],
  "meta": { "dataset_version": "...", "built_at": "...", "row_count": "45231", ... }
}
```

When matched:
```json
{
  "query": "10.1016/j.cell.2009.10.015",
  "matched": true,
  "matches": [
    {
      "record_id": 12345,
      "title": "Example Paper",
      "journal": "Cell",
      "retraction_date": "2010-01-15",
      "retraction_nature": "Retraction",
      "reason": "Data Fabrication",
      "original_paper_doi": "10.1016/j.cell.2009.10.015",
      "retraction_doi": "10.1016/j.cell.2010.01.010",
      ...
    }
  ],
  "meta": { ... }
}
```

---

### `GET /check/pmid/{pmid}`

Check a single PubMed ID.

**Path parameter**

| Parameter | Description |
|---|---|
| `pmid` | Integer PubMed ID |

**Example**
```bash
curl "http://localhost:8000/check/pmid/12345678"
```

**Response** — same shape as `/check/doi/{doi}`, with `"query": 12345678`.

---

### `GET /check/title/{title}`

Check a single paper title (exact, case-insensitive match).

**Path parameter**

| Parameter | Description |
|---|---|
| `title` | URL-encoded paper title |

**Example**
```bash
curl "http://localhost:8000/check/title/Moderation%20of%20gut%20microbiota"
```

**Response** — same shape as `/check/doi/{doi}` with two extra top-level fields:
```json
{
  "query": "Moderation of gut microbiota",
  "match_type": "title",
  "matched": false,
  "matches": [],
  "meta": { "dataset_version": "...", "built_at": "...", "row_count": "45231", ... }
}
```

> **Note:** A `match_type: "title"` field is always present as a reminder to confirm
> positive results with a DOI or PMID.

---

### `POST /check/batch`

Check a list of DOIs and/or PMIDs in a single request.

**Request body**
```json
{
  "dois":  ["10.1038/nature12345", "10.1016/j.cell.2009.10.015"],
  "pmids": [12345678, 99999999]
}
```

**Example**
```bash
curl -X POST "http://localhost:8000/check/batch" \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/nature12345"], "pmids": [12345678]}'
```

**Response**
```json
{
  "results": [
    {
      "query": "10.1038/nature12345",
      "query_type": "doi",
      "matched": false,
      "matches": []
    },
    {
      "query": 12345678,
      "query_type": "pmid",
      "matched": false,
      "matches": []
    }
  ],
  "meta": { ... }
}
```

**Rate limit** — defaults to 60 requests/minute per IP. Override with the `RATE_LIMIT` environment variable.

---

### `POST /check/bib`

Upload a BibTeX file and check every reference.

**Request**
- Content-Type: `multipart/form-data`
- Field: `file` — a `.bib` file

**Example**
```bash
curl -X POST "http://localhost:8000/check/bib" \
  -F "file=@refs.bib"
```

**Response**
```json
{
  "total": 42,
  "retracted": 2,
  "clean": 38,
  "unchecked": 2,
  "results": [
    {
      "key": "smith2010",
      "title": "Example Paper",
      "doi": "10.1016/j.cell.2009.10.015",
      "pmid": null,
      "matched": true,
      "matches": [ { ... } ]
    },
    ...
  ],
  "meta": { ... }
}
```

---

## Response models

### `RecordSummary`

Fields returned inside every `matches` array:

| Field | Type | Description |
|---|---|---|
| `record_id` | `integer` | Retraction Watch record ID |
| `title` | `string \| null` | Title of the retracted paper |
| `journal` | `string \| null` | Journal name |
| `publisher` | `string \| null` | Publisher |
| `author` | `string \| null` | Author list |
| `country` | `string \| null` | Corresponding author country |
| `retraction_date` | `string \| null` | Retraction date (ISO 8601 or partial) |
| `retraction_nature` | `string \| null` | Retraction / Correction / Expression of Concern |
| `reason` | `string \| null` | Semicolon-separated retraction reasons |
| `original_paper_doi` | `string \| null` | Normalised original-paper DOI |
| `original_paper_doi_raw` | `string \| null` | Original-paper DOI as in dataset |
| `retraction_doi` | `string \| null` | Normalised retraction-notice DOI |
| `retraction_doi_raw` | `string \| null` | Retraction-notice DOI as in dataset |
| `original_paper_pmid` | `integer \| null` | Original paper PMID |
| `retraction_pmid` | `integer \| null` | Retraction notice PMID |
| `paywalled` | `string \| null` | `"Yes"` / `"No"` / `"Free"` |
| `urls` | `string \| null` | Semicolon-separated related URLs |

---

## Client examples

=== "curl"
    ```bash
    # Single DOI
    curl "http://localhost:8000/check/doi/10.1038%2Fnature12345" | jq .

    # Single PMID
    curl "http://localhost:8000/check/pmid/12345678" | jq .

    # Title lookup
    curl "http://localhost:8000/check/title/Moderation%20of%20gut%20microbiota" | jq .

    # Batch
    curl -X POST "http://localhost:8000/check/batch" \
      -H "Content-Type: application/json" \
      -d '{"dois": ["10.1038/nature12345"], "pmids": [12345678]}' | jq .
    ```

=== "Python (httpx)"
    ```python
    import httpx, urllib.parse

    BASE = "http://localhost:8000"

    # Single DOI
    r = httpx.get(f"{BASE}/check/doi/10.1038%2Fnature12345")
    print(r.json()["matched"])

    # Title lookup
    t = urllib.parse.quote("Example Paper Title", safe="")
    r = httpx.get(f"{BASE}/check/title/{t}")
    data = r.json()
    if data["matched"]:
        print("RETRACTED — verify with DOI/PMID")

    # Batch
    r = httpx.post(f"{BASE}/check/batch", json={
        "dois": ["10.1038/nature12345"],
        "pmids": [12345678],
    })
    for item in r.json()["results"]:
        print(item["query"], "→", "RETRACTED" if item["matched"] else "ok")
    ```

=== "Python (requests)"
    ```python
    import requests, urllib.parse

    BASE = "http://localhost:8000"

    # Single DOI
    r = requests.get(f"{BASE}/check/doi/10.1038%2Fnature12345")
    print(r.json()["matched"])

    # Title lookup
    t = urllib.parse.quote("Example Paper Title", safe="")
    r = requests.get(f"{BASE}/check/title/{t}")
    data = r.json()
    if data["matched"]:
        print("RETRACTED — verify with DOI/PMID")

    # Batch
    r = requests.post(f"{BASE}/check/batch", json={
        "dois": ["10.1038/nature12345"],
        "pmids": [12345678],
    })
    for item in r.json()["results"]:
        status = "RETRACTED" if item["matched"] else "ok"
        print(f"[{status}] {item['query']}")
    ```

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `RW_DB_PATH` | `/app/data/rw.sqlite` | Path to the SQLite database |
| `RW_CSV_URL` | GitLab URL | Source URL for auto-updates |
| `RATE_LIMIT` | `60/minute` | Rate limit per IP (e.g. `120/minute`) |
| `UPDATE_INTERVAL_HOURS` | `24` | Background auto-update interval |

The API auto-updates the database on a background schedule. The first update runs at startup if the database does not yet exist.
