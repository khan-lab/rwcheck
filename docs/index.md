# RWCheck

**Fast Retraction Watch lookup — REST API, CLI, and Python library.**

RWCheck lets you check DOIs, PubMed IDs, and titles against the [Retraction Watch](https://retractionwatch.com/) dataset in milliseconds. It ships as:

- A **command-line tool** (`rwcheck`) for interactive use and scripting
- A **Python library** (`import rwcheck`) for programmatic access
- A **self-hosted REST API** (FastAPI) with a built-in browser UI

All lookups are served from a local SQLite database built from the [Retraction Watch CSV dataset](https://gitlab.com/crossref/retraction-watch-data), so there are no external API calls at query time.

---

## Quick start

```bash
pip install rwcheck

# Download & build the local database (one-time setup)
rwcheck update

# Check a DOI
rwcheck doi 10.1038/nature12345

# Check a PubMed ID
rwcheck pmid 12345678

# Check by exact title (case-insensitive)
rwcheck title "Exact paper title here"
```

---

## Features

| Feature | Details |
|---|---|
| **DOI lookup** | Normalises URL/bare DOIs; case-insensitive |
| **PMID lookup** | Integer or string input |
| **Title lookup** | Exact case-insensitive title match; warns to verify with DOI/PMID |
| **Batch DOI/PMID** | Text file (one per line) or CSV with `--col` |
| **BibTeX check** | Extracts DOIs & PMIDs from every entry, writes MD + HTML + JSON reports |
| **Search** | Filter dataset by journal, author, country, publisher, reason, or year |
| **Enrich** | Augment a DOI with CrossRef + OpenAlex metadata in one call |
| **Persistent reports** | Browser UI stores `.bib` reports server-side; shareable link, ZIP download, auto-delete after 7 days |
| **Python API** | `check_doi()`, `check_pmid()`, `check_batch()` |
| **REST API** | Versioned (`/api/v1/`) FastAPI with OpenAPI docs, rate limiting, background auto-update |
| **Self-hosted** | Single Docker container; data volume for the SQLite DB |
| **Offline** | No network calls at query time after initial DB build |

---

## How it works

```
retraction_watch.csv (GitLab)
         │
         ▼  rwcheck update / build-rw-db
    data/rw.sqlite
         │
    ┌────┴────────────────────┐
    │   rwcheck CLI           │  ← you run commands here
    │   rwcheck Python API    │  ← import rwcheck
    │   REST API (FastAPI)    │  ← curl / browser
    └─────────────────────────┘
```

1. **Build** — `rwcheck update` downloads the CSV and ingests it into a local SQLite file.
2. **Query** — Every lookup is a fast indexed SQL query against the local DB.
3. **Update** — Run `rwcheck update` periodically (or let the API's background scheduler handle it).

---

## License

MIT. The underlying Retraction Watch dataset is provided by the Center for Scientific Integrity under [their terms](https://retractionwatch.com/the-retraction-watch-database/).
