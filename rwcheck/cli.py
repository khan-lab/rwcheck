"""rwcheck CLI — check DOIs/PMIDs against the Retraction Watch database.

Commands
--------
  doi         Check a single DOI.
  pmid        Check a single PubMed ID.
  batch-doi   Batch-check DOIs from a text or CSV file.
  batch-pmid  Batch-check PMIDs from a text or CSV file.
  batch-bib   Check all references in a BibTeX file; write JSON + Markdown report.
  update      Download the latest dataset and (re)build the local SQLite DB.

Usage examples::

    rwcheck doi 10.1038/nature12345
    rwcheck pmid 12345678
    rwcheck batch-doi papers.txt --out tsv
    rwcheck batch-bib refs.bib
    rwcheck batch-bib refs.bib --report-dir ./reports/
    rwcheck update
    rwcheck update --force
"""

from __future__ import annotations

import csv
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from rwcheck import __version__
from rwcheck.db import (
    get_connection,
    get_meta,
    query_batch,
    query_by_doi,
    query_by_pmid,
    query_by_title,
)
from rwcheck.normalize import normalize_doi, normalize_pmid

app = typer.Typer(
    name="rwcheck",
    help="Check DOIs/PMIDs/BibTeX references against the updated Retraction Watch dataset.",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console(stderr=True)  # status/errors to stderr
out_console = Console()  # results to stdout

# Default local DB path (resolved relative to CWD so it works from the repo root).
_DEFAULT_DB = "data/rw.sqlite"
_DEFAULT_URL = "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"


class OutFormat(str, Enum):
    json = "json"
    tsv = "tsv"
    table = "table"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_db(db: str) -> Path:
    """Return an absolute Path for the DB, raising UsageError if missing."""
    p = Path(db)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def _db_conn(db_path: str):
    """Open DB or exit with a friendly message."""
    try:
        return get_connection(_resolve_db(db_path))
    except FileNotFoundError as exc:
        rprint(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _api_get(api_url: str, path: str) -> dict[str, Any]:
    """GET request to the API; exit gracefully on errors."""
    url = f"{api_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]
    except httpx.HTTPStatusError as exc:
        rprint(f"[red]API error {exc.response.status_code}:[/red] {url}")
        raise typer.Exit(code=1) from exc
    except httpx.RequestError as exc:
        rprint(f"[red]Network error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _api_post(api_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = httpx.post(url, json=body, timeout=30)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]
    except httpx.HTTPStatusError as exc:
        rprint(f"[red]API error {exc.response.status_code}:[/red] {url}")
        raise typer.Exit(code=1) from exc
    except httpx.RequestError as exc:
        rprint(f"[red]Network error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _print_matches(
    query: str,
    matched: bool,
    matches: list[dict],
    meta: dict,
    as_json: bool,
) -> None:
    """Render a single-DOI/PMID result to stdout."""
    result = {"query": query, "matched": matched, "matches": matches, "meta": meta}

    if as_json:
        out_console.print_json(json.dumps(result))
        return

    if matched:
        rprint(
            f"[bold red]RETRACTED[/bold red]  query=[cyan]{query}[/cyan]  ({len(matches)} record(s))"
        )
        for m in matches:
            _print_match_table(m)
    else:
        rprint(
            f"[bold green]NOT FOUND[/bold green]  query=[cyan]{query}[/cyan]  (no retraction records)"
        )

    rprint(
        f"[dim]Dataset: {meta.get('dataset_version', 'n/a')} | "
        f"rows={meta.get('row_count', 'n/a')} | "
        f"built={meta.get('built_at', 'n/a')}[/dim]"
    )


def _print_match_table(m: dict) -> None:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="bold")
    t.add_column()
    for label, key in [
        ("Record ID", "record_id"),
        ("Title", "title"),
        ("Journal", "journal"),
        ("Nature", "retraction_nature"),
        ("Reason", "reason"),
        ("Retraction Date", "retraction_date"),
        ("Original DOI", "original_paper_doi_raw"),
        ("Retraction DOI", "retraction_doi_raw"),
        ("PMID (orig)", "original_paper_pmid"),
        ("PMID (retr)", "retraction_pmid"),
        ("Paywalled", "paywalled"),
    ]:
        val = m.get(key)
        if val is not None and val != "":
            t.add_row(label, str(val))
    out_console.print(t)


def _read_ids_from_file(file: Path, col: str | None) -> list[str]:
    """Read IDs from a plain text file (one per line) or CSV/TSV (via --col)."""
    text = file.read_text(encoding="utf-8")

    # Try CSV/TSV if a column name was given or if it looks like a CSV.
    if col or (file.suffix.lower() in {".csv", ".tsv"}):
        delimiter = "\t" if file.suffix.lower() == ".tsv" else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        if not reader.fieldnames:
            rprint("[red]Error:[/red] Cannot read headers from file.")
            raise typer.Exit(1)
        col_name = col or reader.fieldnames[0]
        if col_name not in reader.fieldnames:
            rprint(
                f"[red]Error:[/red] Column '{col_name}' not found. Available: {reader.fieldnames}"
            )
            raise typer.Exit(1)
        return [row[col_name].strip() for row in reader if row.get(col_name, "").strip()]

    # Plain text: one ID per line.
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def _emit_batch_tsv(results: list[dict]) -> None:
    writer = csv.writer(sys.stdout, delimiter="\t")
    writer.writerow(
        [
            "query",
            "query_type",
            "matched",
            "record_id",
            "title",
            "journal",
            "retraction_nature",
            "reason",
            "retraction_date",
            "original_paper_doi",
            "retraction_doi",
            "original_paper_pmid",
            "retraction_pmid",
        ]
    )
    for r in results:
        if r["matched"]:
            for m in r["matches"]:
                writer.writerow(
                    [
                        r["query"],
                        r.get("query_type", "doi"),
                        True,
                        m.get("record_id"),
                        m.get("title"),
                        m.get("journal"),
                        m.get("retraction_nature"),
                        m.get("reason"),
                        m.get("retraction_date"),
                        m.get("original_paper_doi"),
                        m.get("retraction_doi"),
                        m.get("original_paper_pmid"),
                        m.get("retraction_pmid"),
                    ]
                )
        else:
            writer.writerow(
                [
                    r["query"],
                    r.get("query_type", "doi"),
                    False,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )


# ── Commands ───────────────────────────────────────────────────────────────────


@app.command()
def doi(
    doi_str: Annotated[
        str, typer.Argument(metavar="DOI", help="DOI to check (with or without prefix).")
    ],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output raw JSON.")] = False,
) -> None:
    """Check whether a single DOI appears in the Retraction Watch dataset."""
    if api:
        import urllib.parse

        encoded = urllib.parse.quote(normalize_doi(doi_str) or doi_str, safe="")
        data = _api_get(api, f"api/v1/check/doi/{encoded}")
        _print_matches(doi_str, data["matched"], data["matches"], data["meta"], as_json)
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)
        matches = query_by_doi(conn, doi_str)
        _print_matches(doi_str, bool(matches), matches, meta, as_json)


@app.command()
def pmid(
    pmid_str: Annotated[str, typer.Argument(metavar="PMID", help="PubMed ID to check.")],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output raw JSON.")] = False,
) -> None:
    """Check whether a single PubMed ID appears in the Retraction Watch dataset."""
    norm = normalize_pmid(pmid_str)
    if norm is None:
        rprint(f"[red]Error:[/red] '{pmid_str}' is not a valid PMID.")
        raise typer.Exit(1)

    if api:
        data = _api_get(api, f"api/v1/check/pmid/{norm}")
        _print_matches(pmid_str, data["matched"], data["matches"], data["meta"], as_json)
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)
        matches = query_by_pmid(conn, norm)
        _print_matches(pmid_str, bool(matches), matches, meta, as_json)


@app.command()
def title(
    query: Annotated[
        str, typer.Argument(metavar="TITLE", help="Exact title to search (case-insensitive).")
    ],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output raw JSON.")] = False,
) -> None:
    """Check a paper by exact title match (case-insensitive).

    \b
    Results should be verified with a DOI or PMID — title data quality
    in the Retraction Watch dataset can vary.
    """
    if api:
        import urllib.parse

        encoded = urllib.parse.quote(query, safe="")
        data = _api_get(api, f"api/v1/check/title/{encoded}")
        matches = data.get("matches", [])
        if as_json:
            out_console.print_json(json.dumps(data))
            return
        matched = data.get("matched", False)
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)
        matches = query_by_title(conn, query)
        matched = bool(matches)
        if as_json:
            out_console.print_json(
                json.dumps(
                    {
                        "query": query,
                        "match_type": "title",
                        "matched": matched,
                        "matches": matches,
                        "meta": meta,
                    }
                )
            )
            return

    if not matched:
        rprint(
            f"[bold green]NOT FOUND[/bold green]  query=[cyan]{query}[/cyan]  (no retraction records)"
        )
        return

    rprint(
        f"[bold red]RETRACTED[/bold red]  query=[cyan]{query}[/cyan]  ({len(matches)} record(s))"
    )
    rprint("[yellow]  ⚠ Title match — verify with DOI or PMID.[/yellow]")
    for m in matches:
        _print_match_table(m)

    if not api:
        rprint(
            f"[dim]Dataset: {meta.get('dataset_version', 'n/a')} | "
            f"rows={meta.get('row_count', 'n/a')} | "
            f"built={meta.get('built_at', 'n/a')}[/dim]"
        )


@app.command(name="batch-doi")
def batch_doi(
    file: Annotated[Path, typer.Argument(metavar="FILE", help="Text/CSV file of DOIs.")],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    out: Annotated[OutFormat, typer.Option("--out", help="Output format.")] = OutFormat.table,
    col: Annotated[
        str | None, typer.Option("--col", help="CSV column name containing DOIs.")
    ] = None,
    report_dir: Annotated[
        Path | None,
        typer.Option(
            "--report-dir",
            help="Directory to write report files (default: same dir as input file).",
        ),
    ] = None,
) -> None:
    """Batch-check DOIs from a plain-text file (one per line) or a CSV/TSV file.

    Always writes three report files next to the input (or in --report-dir):

    \b
      <stem>_rwcheck.md   — human-readable Markdown report
      <stem>_rwcheck.json — machine-readable JSON with full match details
      <stem>_rwcheck.html — self-contained HTML report
    """
    from rwcheck.report import batch_results_to_bib_results, write_reports

    if not file.exists():
        rprint(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    dois = _read_ids_from_file(file, col)
    if out == OutFormat.table:
        console.print(f"[dim]Checking {len(dois)} DOI(s)…[/dim]")

    if api:
        data = _api_post(api, "api/v1/check/batch", {"dois": dois, "pmids": []})
        results = data["results"]
        meta = data["meta"]
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)
        results = query_batch(conn, dois=dois, pmids=[])

    if out == OutFormat.json:
        out_console.print_json(json.dumps({"results": results, "meta": meta}))
    elif out == OutFormat.tsv:
        _emit_batch_tsv(results)
    else:
        matched = sum(1 for r in results if r["matched"])
        rprint(f"[bold]Results:[/bold] {matched}/{len(results)} retracted")
        for r in results:
            flag = "[bold red]RETRACTED[/bold red]" if r["matched"] else "[green]ok[/green]"
            rprint(f"  {flag}  {r['query']}")
            if r["matched"]:
                for m in r["matches"]:
                    rprint(
                        f"         → {m.get('retraction_nature')} | {m.get('journal')} | {m.get('retraction_date')}"
                    )
        rprint(
            f"\n[dim]Dataset: {meta.get('dataset_version', 'n/a')} | "
            f"rows={meta.get('row_count', 'n/a')} | built={meta.get('built_at', 'n/a')}[/dim]"
        )

    bib_results = batch_results_to_bib_results(results)
    json_path, md_path, html_path = write_reports(bib_results, meta, file, report_dir)
    if out == OutFormat.table:
        rprint("\nReports written:")
        rprint(f"  Markdown → [cyan]{md_path}[/cyan]")
        rprint(f"  HTML     → [cyan]{html_path}[/cyan]")
        rprint(f"  JSON     → [cyan]{json_path}[/cyan]")


@app.command(name="batch-pmid")
def batch_pmid(
    file: Annotated[Path, typer.Argument(metavar="FILE", help="Text/CSV file of PMIDs.")],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    out: Annotated[OutFormat, typer.Option("--out", help="Output format.")] = OutFormat.table,
    col: Annotated[
        str | None, typer.Option("--col", help="CSV column name containing PMIDs.")
    ] = None,
    report_dir: Annotated[
        Path | None,
        typer.Option(
            "--report-dir",
            help="Directory to write report files (default: same dir as input file).",
        ),
    ] = None,
) -> None:
    """Batch-check PMIDs from a plain-text file or a CSV/TSV file.

    Always writes three report files next to the input (or in --report-dir):

    \b
      <stem>_rwcheck.md   — human-readable Markdown report
      <stem>_rwcheck.json — machine-readable JSON with full match details
      <stem>_rwcheck.html — self-contained HTML report
    """
    from rwcheck.report import batch_results_to_bib_results, write_reports

    if not file.exists():
        rprint(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    raw_ids = _read_ids_from_file(file, col)
    pmids: list[int | str] = [int(x) for x in raw_ids if x.strip().lstrip("-").isdigit()]
    if out == OutFormat.table:
        console.print(
            f"[dim]Checking {len(pmids)} PMID(s) "
            f"(skipped {len(raw_ids) - len(pmids)} non-integer)…[/dim]"
        )

    if api:
        data = _api_post(api, "api/v1/check/batch", {"dois": [], "pmids": pmids})
        results = data["results"]
        meta = data["meta"]
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)
        results = query_batch(conn, dois=[], pmids=pmids)

    if out == OutFormat.json:
        out_console.print_json(json.dumps({"results": results, "meta": meta}))
    elif out == OutFormat.tsv:
        _emit_batch_tsv(results)
    else:
        matched = sum(1 for r in results if r["matched"])
        rprint(f"[bold]Results:[/bold] {matched}/{len(results)} retracted")
        for r in results:
            flag = "[bold red]RETRACTED[/bold red]" if r["matched"] else "[green]ok[/green]"
            rprint(f"  {flag}  {r['query']}")

    bib_results = batch_results_to_bib_results(results)
    json_path, md_path, html_path = write_reports(bib_results, meta, file, report_dir)
    if out == OutFormat.table:
        rprint("\nReports written:")
        rprint(f"  Markdown → [cyan]{md_path}[/cyan]")
        rprint(f"  HTML     → [cyan]{html_path}[/cyan]")
        rprint(f"  JSON     → [cyan]{json_path}[/cyan]")


@app.command(name="batch-bib")
def batch_bib(
    file: Annotated[Path, typer.Argument(metavar="FILE", help="BibTeX (.bib) file to check.")],
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    api: Annotated[str | None, typer.Option("--api", help="Base URL of rwcheck REST API.")] = None,
    report_dir: Annotated[
        Path | None,
        typer.Option(
            "--report-dir", help="Directory to write report files (default: same dir as .bib)."
        ),
    ] = None,
) -> None:
    """Parse a BibTeX file and check every reference against Retraction Watch.

    Extracts DOIs and PubMed IDs from each entry (``doi`` field, ``url`` field
    containing doi.org links, ``pmid`` field, or ``eprint`` + ``eprinttype=pubmed``).

    Always writes two report files next to the input (or in --report-dir):

    \b
      <stem>_rwcheck.md   — human-readable Markdown report
      <stem>_rwcheck.json — machine-readable JSON with full match details

    A summary is also printed to stdout.
    """
    from rwcheck.bib import parse_bib_file
    from rwcheck.report import BibResult, write_reports

    if not file.exists():
        rprint(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    # ── Parse BibTeX ──────────────────────────────────────────────────────────
    console.print(f"[dim]Parsing [cyan]{file}[/cyan]…[/dim]")
    entries = parse_bib_file(file)
    if not entries:
        rprint(f"[yellow]Warning:[/yellow] No BibTeX entries found in '{file}'.")
        raise typer.Exit(0)
    console.print(f"[dim]Found {len(entries)} entries.[/dim]")

    # ── Collect unique DOIs and PMIDs for batch lookup ────────────────────────
    doi_map: dict[str, list[int]] = {}  # normalised_doi → [entry indices]
    pmid_map: dict[int, list[int]] = {}  # pmid → [entry indices]

    for idx, entry in enumerate(entries):
        if entry.doi:
            doi_map.setdefault(entry.doi, []).append(idx)
        if entry.pmid:
            pmid_map.setdefault(entry.pmid, []).append(idx)

    # ── Query ─────────────────────────────────────────────────────────────────
    if api:
        # Use the REST API for lookups.
        bib_results = _batch_bib_via_api(api, entries, doi_map, pmid_map)
        # Fetch meta from API.
        api_meta = _api_get(api, "api/v1/meta")
        meta = {k: str(v) for k, v in api_meta.items()}
    else:
        conn = _db_conn(db)
        meta = get_meta(conn)

        # Single batch DB call for DOIs.
        doi_batch = query_batch(conn, dois=list(doi_map.keys()), pmids=[])
        doi_lookup: dict[str, list[dict]] = {}
        for r in doi_batch:
            doi_lookup[r["query"]] = r["matches"]

        # Single batch DB call for PMIDs.
        pmid_batch = query_batch(conn, dois=[], pmids=list(pmid_map.keys()))
        pmid_lookup: dict[int, list[dict]] = {}
        for r in pmid_batch:
            pmid_lookup[int(r["query"])] = r["matches"]

        # Build BibResult list.
        bib_results = []
        for entry in entries:
            doi_matches = doi_lookup.get(entry.doi, []) if entry.doi else []
            pmid_matches = pmid_lookup.get(entry.pmid, []) if entry.pmid else []
            bib_results.append(
                BibResult(entry=entry, doi_matches=doi_matches, pmid_matches=pmid_matches)
            )

    # ── Write reports ─────────────────────────────────────────────────────────
    json_path, md_path, html_path = write_reports(bib_results, meta, file, report_dir)

    # ── Print summary to stdout ───────────────────────────────────────────────
    n_retracted = sum(1 for r in bib_results if r.matched)
    n_unchecked = sum(1 for r in bib_results if not r.checkable)
    n_clean = len(bib_results) - n_retracted - n_unchecked

    from rich.table import Table

    summary = Table(
        title="Retraction Watch — BibTeX report", box=None, show_header=False, padding=(0, 2)
    )
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Total references", str(len(bib_results)))
    summary.add_row(
        "[bold red]Retracted[/bold red]" if n_retracted else "Retracted",
        f"[bold red]{n_retracted}[/bold red]" if n_retracted else "0",
    )
    summary.add_row("Clean (not found)", str(n_clean))
    summary.add_row("Unchecked (no DOI/PMID)", str(n_unchecked))
    out_console.print(summary)

    if n_retracted:
        rprint("\n[bold red]⚠ Retracted entries:[/bold red]")
        for r in bib_results:
            if r.matched:
                m = r.all_matches[0]
                rprint(
                    f"  [red]✗[/red] [{r.entry.key}] "
                    f"{r.entry.short_author} {r.entry.year or ''} — "
                    f"{m.get('retraction_nature', '')} | {m.get('journal', '')}"
                )

    rprint("\nReports written:")
    rprint(f"  Markdown → [cyan]{md_path}[/cyan]")
    rprint(f"  HTML     → [cyan]{html_path}[/cyan]")
    rprint(f"  JSON     → [cyan]{json_path}[/cyan]")


def _batch_bib_via_api(
    api: str,
    entries: list,
    doi_map: dict,
    pmid_map: dict,
) -> list:
    """Perform batch-bib lookups via the REST API.  Returns list[BibResult]."""
    from rwcheck.report import BibResult

    body: dict = {"dois": list(doi_map.keys()), "pmids": list(pmid_map.keys())}
    data = _api_post(api, "api/v1/check/batch", body)

    doi_lookup: dict[str, list[dict]] = {}
    pmid_lookup: dict[int, list[dict]] = {}
    for r in data.get("results", []):
        if r["query_type"] == "doi":
            doi_lookup[r["query"]] = r["matches"]
        else:
            pmid_lookup[int(r["query"])] = r["matches"]

    results: list[BibResult] = []
    for entry in entries:
        results.append(
            BibResult(
                entry=entry,
                doi_matches=doi_lookup.get(entry.doi, []) if entry.doi else [],
                pmid_matches=pmid_lookup.get(entry.pmid, []) if entry.pmid else [],
            )
        )
    return results


@app.command()
def update(
    db: Annotated[str, typer.Option("--db", help="Path to local SQLite DB.")] = _DEFAULT_DB,
    url: Annotated[str, typer.Option("--url", help="URL of Retraction Watch CSV.")] = _DEFAULT_URL,
    force: Annotated[
        bool, typer.Option("--force", help="Rebuild even if dataset is unchanged.")
    ] = False,
) -> None:
    """Download the latest Retraction Watch CSV and rebuild the local database.

    The update is skipped if the remote file has not changed since the last
    build (detected by SHA-256 comparison).  Use --force to rebuild anyway.
    """
    # Import here to keep startup fast and avoid circular issues.
    from scripts.build_db import build_db

    db_path = _resolve_db(db)
    rprint(f"[bold]rwcheck update[/bold]  target DB → [cyan]{db_path}[/cyan]")
    rprint(f"Source URL → [cyan]{url}[/cyan]")

    build_db(
        csv_path=None,
        url=url,
        db_path=db_path,
        force=force,
    )


@app.callback(invoke_without_command=True)
def _version(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        rprint(f"rwcheck {__version__}")
        raise typer.Exit()
