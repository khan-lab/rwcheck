# CLI Reference

The `rwcheck` command-line tool checks DOIs, PubMed IDs, and paper titles against the local Retraction Watch database.

```
Usage: rwcheck [OPTIONS] COMMAND [ARGS]...

  Check DOIs, PMIDs, and titles against the Retraction Watch dataset.

Options:
  --version   Show version and exit.
  --help      Show this message and exit.

Commands:
  doi    Check a single DOI.
  pmid   Check a single PubMed ID.
  title  Check by exact title (case-insensitive).
  batch  Batch-check DOIs and/or PMIDs from a text or CSV file (auto-detected).
  bib    Check all references in a BibTeX file.
  ris    Check all references in a RIS file.
  update Download the latest dataset and rebuild the local DB.
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
| `--db PATH` | `~/.rwcheck/rw.sqlite` | Path to the local SQLite database |
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
rwcheck doi 10.1126/science.1201068

# URL prefix is stripped automatically
rwcheck doi "https://doi.org/10.1038/nm1491"

# Output JSON
rwcheck doi 10.1126/science.1201068 --json

# Use a different DB path
rwcheck doi 10.1126/science.1201068 --db /opt/rw/data/rw.sqlite

# Delegate to a remote API
rwcheck doi 10.1126/science.1201068 --api http://localhost:8000
```

**Output (table mode)**

```
NOT FOUND  query=10.1126/science.1201068  (no retraction records)
Dataset: abc123... | rows=45231 | built=2024-06-01T12:00:00
```

If the DOI is retracted:

```
RETRACTED  query=10.1126/science.1201068  (1 record(s))
  Record ID         12345
  Title             Coping with Chaos: How Disordered Contexts Promote Stereotyping and Discrimination
  Journal           Science
  Nature            Retraction
  Reason            Data Fabrication
  Retraction Date   2015-05-29
  Original DOI      10.1126/science.1201068
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
rwcheck pmid 21474762
rwcheck pmid 21474762 --json
rwcheck pmid 21474762 --api http://localhost:8000
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
rwcheck title "Coping with Chaos: How Disordered Contexts Promote Stereotyping and Discrimination"
rwcheck title "Genomic signatures to guide the use of chemotherapeutics" --json
rwcheck title "Coping with Chaos: How Disordered Contexts Promote Stereotyping and Discrimination" --api https://rwcheck.khanlab.bio
```

**Output (table mode)**

```
NOT FOUND  query="Coping with Chaos: ..."  (no retraction records)
Dataset: abc123... | rows=45231 | built=2024-06-01T12:00:00
```

If the title matches:

```
RETRACTED  query="Coping with Chaos: How Disordered Contexts Promote Stereotyping and Discrimination"  (1 record(s))
  ⚠ Title match — verify with DOI or PMID.
  Record ID         12345
  Title             Coping with Chaos: How Disordered Contexts Promote Stereotyping and Discrimination
  Journal           Science
  Nature            Retraction
  Reason            Data Fabrication
  Retraction Date   2015-05-29
  Original DOI      10.1126/science.1201068
```

---

## `rwcheck batch`

Batch-check DOIs and/or PMIDs from a plain-text file (one per line) or a CSV/TSV file.
IDs are **auto-detected**: values matching the DOI pattern (`10.xxxx/...`) are treated
as DOIs; pure integers are treated as PMIDs. A single file may mix both types.

```bash
rwcheck batch FILE [--db PATH] [--api URL] [--out FORMAT] [--col COLUMN] [--report-dir DIR]
```

**Arguments**

| Argument | Description |
|---|---|
| `FILE` | Path to a `.txt`, `.csv`, or `.tsv` file containing DOIs and/or PMIDs. |

**Options**

| Option | Default | Description |
|---|---|---|
| `--out FORMAT` | `table` | Output format: `table`, `json`, or `tsv` |
| `--col COLUMN` | first column | CSV column name that contains IDs |
| `--report-dir DIR` | same dir as input | Directory to write report files |

**Input formats**

Plain text (one ID per line, `#` comments ignored, DOIs and PMIDs can be mixed):

```
# My references
10.1126/science.1201068
10.1038/nm1491
https://doi.org/10.1038/nm1491
21474762
```

CSV with an ID column:

```csv
doi,title
10.1126/science.1201068,Coping with Chaos
10.1038/nm1491,Genomic signatures
```

```bash
rwcheck batch papers.csv --col doi
```

**Output formats**

=== "Table"
    ```bash
    rwcheck batch ids.txt
    ```
    Coloured summary printed to stdout + three report files written.

=== "JSON"
    ```bash
    rwcheck batch ids.txt --out json
    ```
    Full JSON to stdout (no report files printed to stdout).

=== "TSV"
    ```bash
    rwcheck batch ids.txt --out tsv > results.tsv
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

## `rwcheck bib`

Parse a BibTeX file and check every reference against Retraction Watch.

```bash
rwcheck bib FILE [--db PATH] [--api URL] [--report-dir DIR]
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
rwcheck bib refs.bib
rwcheck bib refs.bib --report-dir ./output/
rwcheck bib refs.bib --api http://localhost:8000
```

---

## `rwcheck ris`

Parse a RIS file and check every reference against Retraction Watch.

```bash
rwcheck ris FILE [--db PATH] [--api URL] [--report-dir DIR]
```

**Arguments**

| Argument | Description |
|---|---|
| `FILE` | Path to a `.ris` (RIS) file. |

**Options**

| Option | Default | Description |
|---|---|---|
| `--report-dir DIR` | same dir as `.ris` | Directory to write report files |

**DOI/PMID extraction**

For each RIS entry the tool looks for identifiers in:

- `DO` tag (direct DOI field)
- `UR` tag (extracts DOI from `doi.org` URLs; extracts PMID from `pubmed.ncbi.nlm.nih.gov` URLs)
- `AN` tag (accession number — used as PMID when it is a pure integer)

**Output**

A summary table is printed, then retracted entries are listed, then report paths:

```
  Total references          5
  Retracted                 3
  Clean (not found)         1
  Unchecked (no DOI/PMID)   1

⚠ Retracted entries:
  ✗ [smith2020_1] Smith 2020 — Retraction | Journal of Materials

Reports written:
  Markdown → refs_rwcheck.md
  HTML     → refs_rwcheck.html
  JSON     → refs_rwcheck.json
```

**Examples**

```bash
rwcheck ris refs.ris
rwcheck ris refs.ris --report-dir ./output/
rwcheck ris refs.ris --api http://localhost:8000
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
| `--db PATH` | `~/.rwcheck/rw.sqlite` | Target database path |
| `--url URL` | GitLab CSV URL | Source URL for the Retraction Watch CSV |
| `--force` | `False` | Rebuild even if the CSV has not changed (SHA-256 check) |

The update is **skipped** if the remote CSV SHA-256 matches the last build. Use `--force` to rebuild unconditionally.
The `~/.rwcheck/` directory is created automatically on first run.

**Examples**

```bash
# Normal update (skips if unchanged)
rwcheck update

# Force rebuild
rwcheck update --force

# Custom DB path and URL
rwcheck update --db /opt/rw/rw.sqlite --url https://example.com/custom.csv
```
