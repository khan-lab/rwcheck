"""SQLite connection and query helpers.

This module is the single source-of-truth for all database interactions used by
both the CLI (``rwcheck.cli``) and the REST API (``rw_api.main``).

Schema
------
See ``scripts/build_db.py`` for the full DDL.  The columns queried here are:

* ``retractions`` – one row per Retraction Watch record.
* ``meta``        – key/value metadata (version, build timestamp, …).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rwcheck.normalize import normalize_doi, normalize_pmid


# Row factory that returns rows as plain dicts.
def _dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open (read-only WAL) connection to the SQLite database.

    Parameters
    ----------
    db_path:
        Path to the ``rw.sqlite`` file.

    Returns
    -------
    sqlite3.Connection
        Connection with ``row_factory`` set to return dicts.

    Raises
    ------
    FileNotFoundError
        If the database file does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at '{path}'. "
            "Run `rwcheck update` or `python scripts/build_db.py` to build it first."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = _dict_factory
    # Performance pragmas (safe for read-only use).
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-32000;")  # ~32 MB page cache
    return conn


def get_meta(conn: sqlite3.Connection) -> dict[str, str]:
    """Return all rows from the ``meta`` table as a ``{key: value}`` dict."""
    rows = conn.execute("SELECT key, value FROM meta").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate statistics for the ``/stats`` endpoint and landing page.

    Returns
    -------
    dict
        Keys: ``total_records``, ``total_journals``, ``total_countries``,
        ``doi_coverage``, ``pmid_coverage``, ``by_year``, ``top_journals``.
    """
    def scalar(sql: str) -> int:
        # row_factory returns dicts; use a named alias 'n' for all scalar queries.
        row = conn.execute(sql).fetchone()
        return int(row["n"])

    total = scalar("SELECT COUNT(*) AS n FROM retractions")
    journals = scalar(
        "SELECT COUNT(DISTINCT journal) AS n FROM retractions WHERE journal IS NOT NULL"
    )
    # The 'country' column stores semicolon-separated lists (e.g. "China;United States").
    # Aggregate per distinct multi-country string first, then split and accumulate counts
    # per individual country — one query handles both total_countries and by_country.
    country_agg_rows = conn.execute(
        "SELECT country, COUNT(*) AS n FROM retractions "
        "WHERE country IS NOT NULL GROUP BY country"
    ).fetchall()
    country_counts: dict[str, int] = {}
    for row in country_agg_rows:
        for part in row["country"].split(";"):
            c = part.strip()
            if c and c.lower() != "unknown":
                country_counts[c] = country_counts.get(c, 0) + row["n"]
    countries = len(country_counts)
    by_country = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)
    doi_cov = scalar(
        "SELECT COUNT(*) AS n FROM retractions WHERE original_paper_doi IS NOT NULL"
    )
    pmid_cov = scalar(
        "SELECT COUNT(*) AS n FROM retractions WHERE original_paper_pmid IS NOT NULL"
    )

    by_year_rows = conn.execute(
        "SELECT SUBSTR(retraction_date,1,4) AS yr, COUNT(*) AS n "
        "FROM retractions "
        "WHERE retraction_date IS NOT NULL AND LENGTH(retraction_date) >= 4 "
        "  AND SUBSTR(retraction_date,1,4) GLOB '[12][0-9][0-9][0-9]' "
        "GROUP BY yr ORDER BY yr"
    ).fetchall()

    top_journal_rows = conn.execute(
        "SELECT journal, COUNT(*) AS n FROM retractions "
        "WHERE journal IS NOT NULL GROUP BY journal ORDER BY n DESC LIMIT 10"
    ).fetchall()

    return {
        "total_records": total,
        "total_journals": journals,
        "total_countries": countries,
        "doi_coverage": doi_cov,
        "pmid_coverage": pmid_cov,
        "by_year": [[r["yr"], r["n"]] for r in by_year_rows],
        "top_journals": [[r["journal"], r["n"]] for r in top_journal_rows],
        "by_country": [[name, count] for name, count in by_country],
    }


# ── Private helpers ────────────────────────────────────────────────────────────

def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Trim a full DB row to the fields returned in API/CLI output."""
    return {
        "record_id": row["record_id"],
        "title": row["title"],
        "journal": row["journal"],
        "publisher": row["publisher"],
        "author": row["author"],
        "retraction_date": row["retraction_date"],
        "retraction_nature": row["retraction_nature"],
        "reason": row["reason"],
        "retraction_doi": row["retraction_doi"],
        "retraction_doi_raw": row["retraction_doi_raw"],
        "original_paper_doi": row["original_paper_doi"],
        "original_paper_doi_raw": row["original_paper_doi_raw"],
        "original_paper_pmid": row["original_paper_pmid"],
        "retraction_pmid": row["retraction_pmid"],
        "paywalled": row["paywalled"],
        "urls": row["urls"],
        "country": row["country"],
    }


_SELECT = """
    SELECT record_id, title, journal, publisher, author,
           retraction_date, retraction_nature, reason,
           retraction_doi, retraction_doi_raw,
           original_paper_doi, original_paper_doi_raw,
           original_paper_pmid, retraction_pmid,
           paywalled, urls, country
    FROM retractions
"""


# ── Public query API ───────────────────────────────────────────────────────────

def query_by_doi(conn: sqlite3.Connection, doi: str) -> list[dict[str, Any]]:
    """Look up records by DOI (normalised exact match).

    Searches both ``original_paper_doi`` and ``retraction_doi`` columns so that
    a query for a retraction-notice DOI is also surfaced.

    Parameters
    ----------
    conn:
        Open database connection.
    doi:
        Raw DOI string (will be normalised internally).

    Returns
    -------
    list[dict]
        Deduplicated list of matching record summaries (may be empty).
    """
    norm = normalize_doi(doi)
    if norm is None:
        return []

    rows = conn.execute(
        f"{_SELECT} WHERE original_paper_doi = ? OR retraction_doi = ?",
        (norm, norm),
    ).fetchall()

    # Deduplicate by record_id (a DOI could appear in both columns in theory).
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row["record_id"] not in seen:
            seen.add(row["record_id"])
            result.append(_row_to_summary(row))
    return result


def query_by_pmid(conn: sqlite3.Connection, pmid: int | str) -> list[dict[str, Any]]:
    """Look up records by PubMed ID.

    Searches both ``original_paper_pmid`` and ``retraction_pmid`` columns.

    Parameters
    ----------
    conn:
        Open database connection.
    pmid:
        PubMed ID (integer or string; will be normalised internally).

    Returns
    -------
    list[dict]
        Deduplicated list of matching record summaries (may be empty).
    """
    norm = normalize_pmid(pmid)
    if norm is None:
        return []

    rows = conn.execute(
        f"{_SELECT} WHERE original_paper_pmid = ? OR retraction_pmid = ?",
        (norm, norm),
    ).fetchall()

    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row["record_id"] not in seen:
            seen.add(row["record_id"])
            result.append(_row_to_summary(row))
    return result


def query_batch(
    conn: sqlite3.Connection,
    dois: list[str] | None = None,
    pmids: list[int | str] | None = None,
) -> list[dict[str, Any]]:
    """Batch lookup combining DOI and PMID queries.

    Parameters
    ----------
    conn:
        Open database connection.
    dois:
        List of raw DOI strings.
    pmids:
        List of PubMed IDs.

    Returns
    -------
    list[dict]
        Each element has ``query``, ``query_type``, ``matched``, ``matches``.
    """
    results: list[dict[str, Any]] = []

    for doi in dois or []:
        matches = query_by_doi(conn, doi)
        results.append(
            {"query": doi, "query_type": "doi", "matched": bool(matches), "matches": matches}
        )

    for pmid in pmids or []:
        norm = normalize_pmid(pmid)
        matches = query_by_pmid(conn, pmid) if norm else []
        results.append(
            {
                "query": pmid,
                "query_type": "pmid",
                "matched": bool(matches),
                "matches": matches,
            }
        )

    return results
