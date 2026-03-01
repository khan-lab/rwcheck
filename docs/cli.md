# CLI Reference

The `rwcheck` command-line tool checks DOIs, PubMed IDs, and paper titles against the local Retraction Watch database.

```
Usage: rwcheck [OPTIONS] COMMAND [ARGS]...

  Check DOIs, PMIDs, and titles against the Retraction Watch dataset.

Options:
  --version   Show version and exit.
  --help      Show this message and exit.

Commands:
  doi         Check a single DOI.
  pmid        Check a single PubMed ID.
  title       Check by exact title (case-insensitive).
  batch-doi   Batch-check DOIs from a text or CSV file.
  batch-pmid  Batch-check PMIDs from a text or CSV file.
  batch-bib   Check all references in a BibTeX file.
  update      Download the latest dataset and rebuild the local DB.
```

---

## Global options

| Option | Description |
|---|---|
| `--version` | Print `rwcheck <version>` and exit |
| `--help` | Show help and exit |

All commands share two common options for choosing the data source:

| Option | Default | Description |
|---|---|---|
| `--db PATH` | `data/rw.sqlite` | Path to the local SQLite database |
| `--api URL` | *(none)* | Delegate lookups to a remote rwcheck REST API instead of the local DB |

---

## `rwcheck doi`

Check whether a single DOI appears in Retraction Watch.

```bash
rwcheck doi DOI [--db PATH] [--api URL] [--json]
```

**Arguments**

| Argument | Description |
|---|---|
| `DOI` | DOI to check. URL prefixes (`https://doi.org/`) and `doi:` prefixes are stripped automatically. |

**Options**

| Option | Description |
|---|---|
| `--json` | Output raw JSON instead of the formatted table |

**Examples**

```bash
# Basic check
rwcheck doi 10.1038/nature12345

# Output JSON
rwcheck doi 10.1038/nature12345 --json

# Use a different DB path
rwcheck doi 10.1038/nature12345 --db /opt/rw/data/rw.sqlite

# Delegate to a remote API
rwcheck doi 10.1038/nature12345 --api http://localhost:8000
```

**Output (table mode)**

```
NOT FOUND  query=10.1038/nature12345  (no retraction records)
Dataset: abc123... | rows=45231 | built=2024-06-01T12:00:00
```

If the DOI is retracted:

```
RETRACTED  query=10.1016/j.cell.2009.10.015  (1 record(s))
  Record ID         12345
  Title             Example Paper Title
  Journal           Cell
  Nature            Retraction
  Reason            Data Fabrication
  Retraction Date   2010-01-15
  Original DOI      10.1016/j.cell.2009.10.015
  Retraction DOI    10.1016/j.cell.2010.01.010
```

---

## `rwcheck pmid`

Check whether a single PubMed ID appears in Retraction Watch.

```bash
rwcheck pmid PMID [--db PATH] [--api URL] [--json]
```

**Arguments**

| Argument | Description |
|---|---|
| `PMID` | PubMed ID (integer or numeric string). |

**Options**

Same as `rwcheck doi`.

**Examples**

```bash
rwcheck pmid 12345678
rwcheck pmid 12345678 --json
rwcheck pmid 12345678 --api http://localhost:8000
```

---

## `rwcheck title`

Check whether a paper title appears in Retraction Watch (exact, case-insensitive match).

```bash
rwcheck title TITLE [--db PATH] [--api URL] [--json]
```

**Arguments**

| Argument | Description |
|---|---|
| `TITLE` | Exact paper title to search. Enclose in quotes if it contains spaces. |

**Options**

| Option | Description |
|---|---|
| `--json` | Output raw JSON instead of the formatted table |

> **Note:** Title matching is exact and case-insensitive. When a match is found a
> warning is displayed reminding you to confirm the result with a DOI or PMID.

**Examples**

```bash
rwcheck title "Moderation of gut microbiota is associated with..."
rwcheck title "Example Paper Title" --json
rwcheck title "Example Paper Title" --api https://rwcheck.khanlab.bio
```

**Output (table mode)**

```
NOT FOUND  query="Example Paper Title"  (no retraction records)
Dataset: abc123... | rows=45231 | built=2024-06-01T12:00:00
```

If the title matches:

```
RETRACTED  query="Example Paper Title"  (1 record(s))
  ⚠ Title match — verify with DOI or PMID.
  Record ID         12345
  Title             Example Paper Title
  Journal           Cell
  Nature            Retraction
  Reason            Data Fabrication
  Retraction Date   2010-01-15
  Original DOI      10.1016/j.cell.2009.10.015
```

---

## `rwcheck batch-doi`

Batch-check DOIs from a plain-text file (one per line) or a CSV/TSV file.

```bash
rwcheck batch-doi FILE [--db PATH] [--api URL] [--out FORMAT] [--col COLUMN] [--report-dir DIR]
```

**Arguments**

| Argument | Description |
|---|---|
| `FILE` | Path to a `.txt`, `.csv`, or `.tsv` file containing DOIs. |

**Options**

| Option | Default | Description |
|---|---|---|
| `--out FORMAT` | `table` | Output format: `table`, `json`, or `tsv` |
| `--col COLUMN` | first column | CSV column name that contains DOIs |
| `--report-dir DIR` | same dir as input | Directory to write report files |

**Input formats**

Plain text (one DOI per line, `#` comments ignored):

```
# My references
10.1038/nature12345
10.1016/j.cell.2009.10.015
https://doi.org/10.1007/s00248-019-01449-w
```

CSV with a `doi` column:

```csv
doi,title
10.1038/nature12345,Paper 1
10.1016/j.cell.2009.10.015,Paper 2
```

```bash
rwcheck batch-doi papers.csv --col doi
```

**Output formats**

=== "Table"
    ```bash
    rwcheck batch-doi dois.txt
    ```
    Coloured summary printed to stdout + three report files written.

=== "JSON"
    ```bash
    rwcheck batch-doi dois.txt --out json
    ```
    Full JSON to stdout (no report files printed to stdout).

=== "TSV"
    ```bash
    rwcheck batch-doi dois.txt --out tsv > results.tsv
    ```
    One row per match; header row included.

**Report files**

Three files are always written (regardless of `--out`):

| File | Description |
|---|---|
| `<stem>_rwcheck.md` | Human-readable Markdown report |
| `<stem>_rwcheck.html` | Self-contained HTML report |
| `<stem>_rwcheck.json` | Machine-readable JSON with full match details |

---

## `rwcheck batch-pmid`

Batch-check PMIDs from a plain-text file or CSV/TSV. Same interface as `batch-doi`.

```bash
rwcheck batch-pmid FILE [--db PATH] [--api URL] [--out FORMAT] [--col COLUMN] [--report-dir DIR]
```

Non-integer lines are silently skipped. A count of skipped entries is shown in table mode.

**Examples**

```bash
# Plain text file
rwcheck batch-pmid pmids.txt

# CSV with explicit column
rwcheck batch-pmid refs.csv --col pubmed_id --out tsv > results.tsv

# Write reports to a specific directory
rwcheck batch-pmid pmids.txt --report-dir ./reports/
```

---

## `rwcheck batch-bib`

Parse a BibTeX file and check every reference against Retraction Watch.

```bash
rwcheck batch-bib FILE [--db PATH] [--api URL] [--report-dir DIR]
```

**Arguments**

| Argument | Description |
|---|---|
| `FILE` | Path to a `.bib` (BibTeX) file. |

**Options**

| Option | Default | Description |
|---|---|---|
| `--report-dir DIR` | same dir as `.bib` | Directory to write report files |

**DOI/PMID extraction**

For each BibTeX entry the tool looks for identifiers in:

- `doi` field
- `url` field (extracts DOI from `doi.org` URLs)
- `pmid` field
- `eprint` field when `eprinttype = {pubmed}`

**Output**

A summary table is printed, then retracted entries are listed, then report paths:

```
  Total references         42
  Retracted                 2
  Clean (not found)        38
  Unchecked (no DOI/PMID)   2

⚠ Retracted entries:
  ✗ [smith2010] Smith et al. 2010 — Retraction | Cell

Reports written:
  Markdown → refs_rwcheck.md
  HTML     → refs_rwcheck.html
  JSON     → refs_rwcheck.json
```

**Examples**

```bash
rwcheck batch-bib refs.bib
rwcheck batch-bib refs.bib --report-dir ./output/
rwcheck batch-bib refs.bib --api http://localhost:8000
```

---

## `rwcheck update`

Download the latest Retraction Watch CSV and rebuild the local database.

```bash
rwcheck update [--db PATH] [--url URL] [--force]
```

**Options**

| Option | Default | Description |
|---|---|---|
| `--db PATH` | `data/rw.sqlite` | Target database path |
| `--url URL` | GitLab CSV URL | Source URL for the Retraction Watch CSV |
| `--force` | `False` | Rebuild even if the CSV has not changed (SHA-256 check) |

The update is **skipped** if the remote CSV SHA-256 matches the last build. Use `--force` to rebuild unconditionally.

**Examples**

```bash
# Normal update (skips if unchanged)
rwcheck update

# Force rebuild
rwcheck update --force

# Custom DB path and URL
rwcheck update --db /opt/rw/rw.sqlite --url https://example.com/custom.csv
```
