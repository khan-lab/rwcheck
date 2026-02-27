"""Build (or rebuild) the rwcheck SQLite database from the Retraction Watch CSV.

Usage (standalone)
------------------
::

    # From a local file (default: data/cached_rw.csv):
    python scripts/build_db.py --csv retraction_watch.csv

    # Download from the canonical GitLab URL:
    python scripts/build_db.py --url https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv

    # Force rebuild even if the dataset has not changed:
    python scripts/build_db.py --csv retraction_watch.csv --force

This module is also imported by ``rwcheck.cli`` (``update`` command) and
``rw_api.main`` (background scheduler), so the core logic lives in the
``build_db()`` function rather than only in ``__main__``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from rwcheck.normalize import normalize_doi, normalize_pmid

console = Console(stderr=True)

# Canonical source URL.
DEFAULT_URL = (
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
)
DEFAULT_DB = Path("data/rw.sqlite")
DEFAULT_CACHED_CSV = Path("data/cached_rw.csv")

# SQLite batch insert size.
_BATCH_SIZE = 500

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS retractions (
    record_id            INTEGER PRIMARY KEY,
    title                TEXT,
    subject              TEXT,
    institution          TEXT,
    journal              TEXT,
    publisher            TEXT,
    country              TEXT,
    author               TEXT,
    urls                 TEXT,
    article_type         TEXT,
    retraction_date      TEXT,
    retraction_doi       TEXT,       -- normalised
    retraction_doi_raw   TEXT,       -- original from CSV
    retraction_pmid      INTEGER,    -- NULL when absent/0
    original_paper_date  TEXT,
    original_paper_doi   TEXT,       -- normalised
    original_paper_doi_raw TEXT,     -- original from CSV
    original_paper_pmid  INTEGER,    -- NULL when absent/0
    retraction_nature    TEXT,
    reason               TEXT,
    paywalled            TEXT,
    notes                TEXT,
    raw_json             TEXT        -- full CSV row as JSON
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for O(log n) lookups.
CREATE INDEX IF NOT EXISTS idx_orig_doi  ON retractions (original_paper_doi);
CREATE INDEX IF NOT EXISTS idx_ret_doi   ON retractions (retraction_doi);
CREATE INDEX IF NOT EXISTS idx_orig_pmid ON retractions (original_paper_pmid);
CREATE INDEX IF NOT EXISTS idx_ret_pmid  ON retractions (retraction_pmid);
"""

# ── Date normalisation ─────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_date(raw: str) -> str | None:
    """Convert MM/DD/YYYY HH:MM (US format from Retraction Watch) → ISO-8601 date."""
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    month, day, year = m.groups()
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _detect_delimiter(sample: str) -> str:
    """Return ',' or '\\t' based on which appears more in the first line."""
    first_line = sample.split("\n", 1)[0]
    return "\t" if first_line.count("\t") > first_line.count(",") else ","


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_stream(stream: IO[bytes]) -> tuple[str, bytes]:
    """Return (hex_digest, full_content) for a byte stream."""
    h = hashlib.sha256()
    buf = io.BytesIO()
    for chunk in iter(lambda: stream.read(65536), b""):
        h.update(chunk)
        buf.write(chunk)
    return h.hexdigest(), buf.getvalue()


# ── Download ───────────────────────────────────────────────────────────────────

def _download_csv(url: str, dest: Path) -> str:
    """Download *url* to *dest*, return SHA-256 hex digest."""
    console.print(f"[dim]Downloading → {url}[/dim]")
    dest.parent.mkdir(parents=True, exist_ok=True)

    h = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        with (
            dest.open("wb") as fout,
            Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress,
        ):
            task = progress.add_task("Downloading…", total=total or None)
            for chunk in resp.iter_bytes(chunk_size=65536):
                fout.write(chunk)
                h.update(chunk)
                progress.update(task, advance=len(chunk))

    return h.hexdigest()


# ── Stored hash ────────────────────────────────────────────────────────────────

def _get_stored_sha256(db_path: Path) -> str | None:
    """Return the ``csv_sha256`` stored in an existing DB, or None."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM meta WHERE key='csv_sha256'"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


# ── Ingestion ──────────────────────────────────────────────────────────────────

def _ingest(conn: sqlite3.Connection, csv_path: Path, source_url: str, sha256: str) -> int:
    """Read *csv_path*, populate the DB, write meta. Return row count."""
    text = csv_path.read_text(encoding="utf-8", errors="replace")
    delimiter = _detect_delimiter(text)

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    # Clear existing data for a clean rebuild.
    conn.execute("DELETE FROM retractions")
    conn.execute("DELETE FROM meta")

    batch: list[tuple] = []
    row_count = 0

    _insert_sql = """
        INSERT OR REPLACE INTO retractions VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
    """

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Ingesting rows…", total=None)

        for raw in reader:
            record_id_raw = raw.get("Record ID", "").strip()
            if not record_id_raw:
                continue
            try:
                record_id = int(record_id_raw)
            except ValueError:
                continue

            ret_doi_raw = raw.get("RetractionDOI", "").strip()
            orig_doi_raw = raw.get("OriginalPaperDOI", "").strip()

            batch.append((
                record_id,
                raw.get("Title", "").strip() or None,
                raw.get("Subject", "").strip() or None,
                raw.get("Institution", "").strip() or None,
                raw.get("Journal", "").strip() or None,
                raw.get("Publisher", "").strip() or None,
                raw.get("Country", "").strip() or None,
                raw.get("Author", "").strip() or None,
                raw.get("URLS", "").strip() or None,
                raw.get("ArticleType", "").strip() or None,
                _parse_date(raw.get("RetractionDate", "")),
                normalize_doi(ret_doi_raw),
                ret_doi_raw or None,
                normalize_pmid(raw.get("RetractionPubMedID", "0")),
                _parse_date(raw.get("OriginalPaperDate", "")),
                normalize_doi(orig_doi_raw),
                orig_doi_raw or None,
                normalize_pmid(raw.get("OriginalPaperPubMedID", "0")),
                raw.get("RetractionNature", "").strip() or None,
                raw.get("Reason", "").strip() or None,
                raw.get("Paywalled", "").strip() or None,
                raw.get("Notes", "").strip() or None,
                json.dumps(dict(raw), ensure_ascii=False),
            ))
            row_count += 1
            progress.update(task, advance=1)

            if len(batch) >= _BATCH_SIZE:
                conn.executemany(_insert_sql, batch)
                batch.clear()

        if batch:
            conn.executemany(_insert_sql, batch)

    # Write metadata.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    short_version = sha256[:16]
    meta_rows = [
        ("dataset_version", short_version),
        ("built_at", now_iso),
        ("row_count", str(row_count)),
        ("source_url", source_url),
        ("csv_sha256", sha256),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", meta_rows
    )
    conn.commit()

    return row_count


# ── Public API ─────────────────────────────────────────────────────────────────

def build_db(
    csv_path: Path | str | None,
    url: str | None,
    db_path: Path | str = DEFAULT_DB,
    force: bool = False,
) -> None:
    """Build or update the SQLite database.

    Priority: ``csv_path`` (local file) > ``url`` (download) > default cached CSV.

    Parameters
    ----------
    csv_path:
        Path to a local CSV file.  If ``None``, ``url`` is used.
    url:
        Remote URL of the Retraction Watch CSV.  Used when ``csv_path`` is
        ``None``; the file is downloaded to ``data/cached_rw.csv``.
    db_path:
        Output SQLite file path.
    force:
        Rebuild even if the CSV hash matches the stored one.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Resolve source ────────────────────────────────────────────────────────
    if csv_path:
        csv_path = Path(csv_path)
        source_url = str(csv_path.resolve())
        sha256 = _sha256_file(csv_path)
        console.print(f"[dim]Local CSV SHA-256: {sha256[:16]}…[/dim]")
    elif url:
        cached = DEFAULT_CACHED_CSV
        sha256 = _download_csv(url, cached)
        csv_path = cached
        source_url = url
        console.print(f"[dim]Downloaded SHA-256: {sha256[:16]}…[/dim]")
    else:
        # Fallback to cached CSV.
        if not DEFAULT_CACHED_CSV.exists():
            console.print(
                "[red]Error:[/red] No CSV source provided and no cached file found. "
                "Pass --csv or --url."
            )
            sys.exit(1)
        csv_path = DEFAULT_CACHED_CSV
        source_url = str(csv_path.resolve())
        sha256 = _sha256_file(csv_path)

    # ── Check if update needed ────────────────────────────────────────────────
    if not force:
        stored = _get_stored_sha256(db_path)
        if stored and stored == sha256:
            console.print(
                "[green]Dataset unchanged[/green] (SHA-256 matches). "
                "Use --force to rebuild anyway."
            )
            return

    # ── Build ─────────────────────────────────────────────────────────────────
    console.print(f"[bold]Building DB[/bold] → [cyan]{db_path}[/cyan]")

    # Write to a temp file first for atomic swap.
    tmp = db_path.with_suffix(".tmp.sqlite")
    tmp.unlink(missing_ok=True)

    conn = sqlite3.connect(str(tmp))
    try:
        conn.executescript(_DDL)
        row_count = _ingest(conn, csv_path, source_url, sha256)
    finally:
        conn.close()

    # Atomic replace (POSIX rename is atomic within the same filesystem).
    tmp.replace(db_path)

    console.print(
        f"[bold green]Done.[/bold green] "
        f"Ingested [cyan]{row_count:,}[/cyan] rows → [cyan]{db_path}[/cyan]"
    )


# ── CLI entry-point ────────────────────────────────────────────────────────────

def main() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Build the rwcheck SQLite database.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--csv", metavar="PATH", help="Local CSV/TSV file.")
    src.add_argument(
        "--url",
        metavar="URL",
        nargs="?",
        const=DEFAULT_URL,
        help="Download from URL (default: canonical GitLab URL).",
    )
    parser.add_argument("--db", metavar="PATH", default=str(DEFAULT_DB), help="Output DB path.")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if unchanged.")
    args = parser.parse_args()

    build_db(
        csv_path=args.csv,
        url=args.url,
        db_path=args.db,
        force=args.force,
    )


if __name__ == "__main__":
    main()
