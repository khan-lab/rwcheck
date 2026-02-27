# Python API

RWCheck exposes a simple Python API for programmatic lookups. All three functions query the local SQLite database directly — no HTTP round-trips.

## Installation

```bash
pip install rwcheck
```

## Import

```python
import rwcheck

# or import individual functions
from rwcheck import check_doi, check_pmid, check_batch
```

## Database path resolution

All functions accept an optional `db_path` keyword argument. When omitted the path is resolved in order:

1. `db_path` keyword argument
2. `RW_DB_PATH` environment variable
3. `"data/rw.sqlite"` (relative to the current working directory)

```python
import os

# Set globally for the process
os.environ["RW_DB_PATH"] = "/opt/rwcheck/rw.sqlite"

result = rwcheck.check_doi("10.1038/nature12345")  # uses /opt/rwcheck/rw.sqlite
```

---

## Reference

::: rwcheck.api.check_doi

---

::: rwcheck.api.check_pmid

---

::: rwcheck.api.check_batch

---

## Return shapes

### `check_doi` / `check_pmid`

```python
{
    "query":   "10.1038/nature12345",  # original query string
    "matched": False,                  # True if ≥1 record found
    "matches": [],                     # list of RecordSummary dicts (see below)
    "meta": {
        "dataset_version": "abc123def456...",  # first 16 hex chars of CSV SHA-256
        "built_at":        "2024-06-01T12:00:00",
        "row_count":       "45231",
        "source_url":      "https://gitlab.com/...",
        "csv_sha256":      "abc123...",
    }
}
```

### `check_batch`

Returns a **JSON string** (not a dict) for easy serialisation:

```python
json_str = rwcheck.check_batch(
    dois=["10.1038/nature12345"],
    pmids=[12345678],
)
import json
data = json.loads(json_str)
# data["results"]  → list of per-query dicts
# data["meta"]     → dataset provenance dict
```

Each item in `results`:

```python
{
    "query":      "10.1038/nature12345",
    "query_type": "doi",    # "doi" or "pmid"
    "matched":    False,
    "matches":    [],
}
```

### Match record fields

| Field | Type | Description |
|---|---|---|
| `record_id` | `int` | Retraction Watch internal record ID |
| `title` | `str \| None` | Title of the retracted paper |
| `journal` | `str \| None` | Journal name |
| `publisher` | `str \| None` | Publisher name |
| `author` | `str \| None` | Author list |
| `country` | `str \| None` | Country of corresponding author |
| `retraction_date` | `str \| None` | Date of retraction (YYYY-MM-DD or partial) |
| `retraction_nature` | `str \| None` | `"Retraction"`, `"Correction"`, `"Expression of Concern"`, … |
| `reason` | `str \| None` | Semicolon-separated retraction reasons |
| `original_paper_doi` | `str \| None` | Normalised DOI of the original paper |
| `original_paper_doi_raw` | `str \| None` | DOI as it appears in the CSV |
| `retraction_doi` | `str \| None` | Normalised DOI of the retraction notice |
| `retraction_doi_raw` | `str \| None` | Retraction notice DOI as in CSV |
| `original_paper_pmid` | `int \| None` | PubMed ID of the original paper |
| `retraction_pmid` | `int \| None` | PubMed ID of the retraction notice |
| `paywalled` | `str \| None` | `"Yes"` / `"No"` / `"Free"` |
| `urls` | `str \| None` | Semicolon-separated related URLs |

---

## Examples

### Check a single DOI

```python
import rwcheck

result = rwcheck.check_doi("10.1038/nature12345")

if result["matched"]:
    for match in result["matches"]:
        print(match["retraction_nature"], "|", match["journal"])
        print("Retracted:", match["retraction_date"])
else:
    print("Not found in Retraction Watch")
```

### Check a single PMID

```python
result = rwcheck.check_pmid(12345678)

print("Matched:", result["matched"])
print("Dataset built:", result["meta"]["built_at"])
```

### Batch check (DOIs + PMIDs)

```python
import json
import rwcheck

json_str = rwcheck.check_batch(
    dois=[
        "10.1038/nature12345",
        "10.1016/j.cell.2009.10.015",
    ],
    pmids=[12345678, 99999999],
)

data = json.loads(json_str)
for item in data["results"]:
    status = "RETRACTED" if item["matched"] else "ok"
    print(f"[{status}] {item['query_type']}:{item['query']}")
```

### Use a custom DB path

```python
result = rwcheck.check_doi(
    "10.1038/nature12345",
    db_path="/opt/rwcheck/rw.sqlite",
)
```

### Pipeline integration

```python
import csv
import json
import rwcheck

with open("dois.csv") as f:
    reader = csv.DictReader(f)
    dois = [row["doi"] for row in reader]

data = json.loads(rwcheck.check_batch(dois=dois))
retracted = [r for r in data["results"] if r["matched"]]

print(f"{len(retracted)}/{len(dois)} DOIs found in Retraction Watch")
for r in retracted:
    print(" -", r["query"])
```
