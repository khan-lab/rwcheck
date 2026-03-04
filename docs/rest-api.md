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

## URL structure

All versioned endpoints live under `/api/v1/`. The two root-level routes stay unversioned:

| Path | Notes |
|---|---|
| `/` | Browser UI (NiceGUI SPA) |
| `/health` | Liveness check |
| `/docs` | Swagger / OpenAPI UI |
| `/redoc` | ReDoc UI |
| `/api/v1/*` | All data endpoints |

---

## Endpoints

### `GET /health`

Liveness check. Returns `200 OK` if the service is running.

**Response**
```json
{ "status": "ok", "db_exists": true }
```

---

### `GET /api/v1/meta`

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

### `GET /api/v1/stats`

Aggregate statistics about the dataset.

**Response**
```json
{
  "total_records": 45231,
  "total_journals": 8412,
  "total_countries": 94,
  "total_authors": 112345,
  "doi_coverage": 39801,
  "pmid_coverage": 32100,
  "by_year": [["2000", 12], ["2001", 34], "..."],
  "top_journals": [["PLOS ONE", 2100], "..."],
  "by_country": [["United States", 12000], "..."],
  "meta": { "..." }
}
```

---

### `GET /api/v1/check/doi/{doi}`

Check a single DOI.

**Path parameter**

| Parameter | Description |
|---|---|
| `doi` | DOI string (bare or URL-encoded). URL prefixes (`https://doi.org/`) and `doi:` prefixes are stripped automatically. DOIs containing slashes are passed directly. |

**Example**
```bash
curl "http://localhost:8000/api/v1/check/doi/10.1038%2Fnature12345"
# slashes in the DOI do not need encoding:
curl "http://localhost:8000/api/v1/check/doi/10.1051/e3sconf/202453804025"
```

**Response**
```json
{
  "query": "10.1038/nature12345",
  "matched": false,
  "matches": [],
  "meta": { "dataset_version": "...", "built_at": "...", "row_count": "45231" }
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
      "retraction_doi": "10.1016/j.cell.2010.01.010"
    }
  ],
  "meta": { "..." }
}
```

---

### `GET /api/v1/check/pmid/{pmid}`

Check a single PubMed ID.

**Path parameter**

| Parameter | Description |
|---|---|
| `pmid` | Integer PubMed ID |

**Example**
```bash
curl "http://localhost:8000/api/v1/check/pmid/12345678"
```

**Response** — same shape as `/api/v1/check/doi/{doi}`, with `"query": 12345678`.

---

### `GET /api/v1/check/title/{title}`

Check a single paper title (exact, case-insensitive match).

**Path parameter**

| Parameter | Description |
|---|---|
| `title` | URL-encoded paper title |

**Example**
```bash
curl "http://localhost:8000/api/v1/check/title/Moderation%20of%20gut%20microbiota"
```

**Response** — same shape as `/api/v1/check/doi/{doi}` with two extra top-level fields:
```json
{
  "query": "Moderation of gut microbiota",
  "match_type": "title",
  "matched": false,
  "matches": [],
  "meta": { "..." }
}
```

> **Note:** A `match_type: "title"` field is always present as a reminder to confirm
> positive results with a DOI or PMID.

---

### `POST /api/v1/check/batch`

Check a list of DOIs and/or PMIDs in a single request. Maximum 500 items per call.

**Request body**
```json
{
  "dois":  ["10.1038/nature12345", "10.1016/j.cell.2009.10.015"],
  "pmids": [12345678, 99999999]
}
```

**Example**
```bash
curl -X POST "http://localhost:8000/api/v1/check/batch" \
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
  "meta": { "..." }
}
```

---

### `POST /api/v1/check/bib`

Upload a BibTeX file and check every reference. Returns a JSON summary; for full HTML/Markdown/JSON reports use the browser UI.

**Request**
- Content-Type: `multipart/form-data`
- Field: `file` — a `.bib` file

**Example**
```bash
curl -X POST "http://localhost:8000/api/v1/check/bib" \
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
      "matches": [ { "..." } ]
    }
  ],
  "meta": { "..." }
}
```

---

### `GET /api/v1/search`

Search the dataset by journal, author, country, publisher, reason, or year. At least one filter is required.

**Query parameters**

| Parameter | Match type | Description |
|---|---|---|
| `journal` | Exact (case-insensitive) | Journal name |
| `author` | Partial (`LIKE`) | Author substring |
| `country` | Partial (`LIKE`) | Country substring |
| `publisher` | Partial (`LIKE`) | Publisher substring |
| `reason` | Partial (`LIKE`) | Retraction reason substring |
| `year` | Prefix match | Retraction year (e.g. `2020`) |
| `limit` | — | Max results (default `100`, max `500`) |
| `offset` | — | Pagination offset (default `0`) |

**Example**
```bash
# All retractions in PLOS ONE
curl "http://localhost:8000/api/v1/search?journal=PLOS+ONE"

# Data fabrication retractions in the USA, paginated
curl "http://localhost:8000/api/v1/search?reason=fabrication&country=United+States&limit=50&offset=0"
```

**Response**
```json
{
  "total": 312,
  "limit": 100,
  "offset": 0,
  "results": [
    {
      "record_id": 12345,
      "title": "Example Paper",
      "journal": "PLOS ONE",
      "retraction_date": "2020-03-01",
      "reason": "Data Fabrication",
      "country": "United States"
    }
  ],
  "meta": { "..." }
}
```

---

### `GET /api/v1/enrich/doi/{doi}`

Enrich a DOI with metadata from CrossRef and OpenAlex, alongside the retraction status. External API calls are made in parallel and time out after 10 seconds each.

**Path parameter**

| Parameter | Description |
|---|---|
| `doi` | DOI string (same normalisation as `/api/v1/check/doi`) |

**Example**
```bash
curl "http://localhost:8000/api/v1/enrich/doi/10.9999%2Fjmat.2020.001234"
```

**Response**
```json
{
  "doi": "10.9999/jmat.2020.001234",
  "retraction_status": {
    "matched": true,
    "matches": [ { "..." } ]
  },
  "crossref": {
    "title": "Example Paper",
    "container-title": ["Journal of Materials"],
    "published": { "date-parts": [[2020, 3, 1]] },
    "author": [ { "family": "Smith", "given": "J." } ]
  },
  "openalex": {
    "id": "https://openalex.org/W1234567890",
    "cited_by_count": 42,
    "open_access": { "is_oa": true, "oa_url": "https://..." }
  }
}
```

Fields are `null` when the external API is unavailable or times out.

---

### `GET /api/v1/reports/{id}/html`

Serve the HTML report for a previously uploaded `.bib` file (generated via the browser UI). The report ID is a UUID assigned at upload time.

**Example**
```bash
curl "http://localhost:8000/api/v1/reports/550e8400-e29b-41d4-a716-446655440000/html"
```

Returns an `text/html` response with the self-contained report. Returns `404` if the report has been deleted or expired.

---

### `GET /api/v1/reports/{id}/zip`

Download all three report files (`.json`, `.md`, `.html`) as a ZIP archive.

**Example**
```bash
curl -OJ "http://localhost:8000/api/v1/reports/550e8400-e29b-41d4-a716-446655440000/zip"
```

Returns a `application/zip` stream. Returns `404` if the report does not exist.

---

### `DELETE /api/v1/reports/{id}`

Delete a report from the server immediately.

**Example**
```bash
curl -X DELETE "http://localhost:8000/api/v1/reports/550e8400-e29b-41d4-a716-446655440000"
```

**Response**
```json
{ "deleted": true }
```

Returns `404` if the report does not exist. Reports are also automatically deleted after `RW_REPORT_MAX_AGE_DAYS` days (default: 7).

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
    BASE="http://localhost:8000/api/v1"

    # Single DOI
    curl "$BASE/check/doi/10.1038%2Fnature12345" | jq .

    # Single PMID
    curl "$BASE/check/pmid/12345678" | jq .

    # Title lookup
    curl "$BASE/check/title/Moderation%20of%20gut%20microbiota" | jq .

    # Batch
    curl -X POST "$BASE/check/batch" \
      -H "Content-Type: application/json" \
      -d '{"dois": ["10.1038/nature12345"], "pmids": [12345678]}' | jq .

    # Search
    curl "$BASE/search?journal=PLOS+ONE&limit=10" | jq .

    # Enrich
    curl "$BASE/enrich/doi/10.1038%2Fnature12345" | jq .
    ```

=== "Python (httpx)"
    ```python
    import httpx, urllib.parse

    BASE = "http://localhost:8000/api/v1"

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

    # Search
    r = httpx.get(f"{BASE}/search", params={"reason": "fabrication", "limit": 20})
    print(r.json()["total"], "records found")

    # Enrich
    r = httpx.get(f"{BASE}/enrich/doi/10.1038%2Fnature12345")
    print(r.json()["crossref"])
    ```

=== "Python (requests)"
    ```python
    import requests, urllib.parse

    BASE = "http://localhost:8000/api/v1"

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

    # Search
    r = requests.get(f"{BASE}/search", params={"journal": "PLOS ONE", "limit": 50})
    print(r.json()["total"], "records found")
    ```

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `RW_DB_PATH` | `/app/data/rw.sqlite` | Path to the SQLite database |
| `RW_CSV_URL` | GitLab URL | Source URL for auto-updates |
| `RATE_LIMIT` | `60/minute` | Rate limit per IP (e.g. `120/minute`) |
| `UPDATE_INTERVAL_HOURS` | `24` | Background auto-update interval |
| `RW_REPORTS_DIR` | `rw_reports` | Directory for server-side `.bib` reports |
| `RW_REPORT_MAX_AGE_DAYS` | `7` | Days before uploaded reports are auto-deleted |
| `RW_PUBLIC_HOST` | *(empty)* | Public base URL used in share links (e.g. `https://rwcheck.example.com`) |

The API auto-updates the database on a background schedule. The first update runs at startup if the database does not yet exist.
