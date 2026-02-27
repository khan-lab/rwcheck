"""High-level Python API for rwcheck.

Usage::

    from rwcheck.api import check_doi, check_pmid, check_batch

    # Single lookup — returns dict
    result = check_doi("10.1038/nature12345")
    if result["matched"]:
        print(result["matches"][0]["retraction_date"])

    # Batch lookup — returns JSON string
    json_str = check_batch(dois=["10.1038/nature12345"], pmids=[12345678])

All three functions accept an optional ``db_path`` keyword argument.
When omitted the path is resolved from the ``RW_DB_PATH`` environment
variable (falling back to ``"data/rw.sqlite"``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rwcheck.db import get_connection, get_meta, query_batch, query_by_doi, query_by_pmid


def _resolve_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("RW_DB_PATH")
    if env:
        return Path(env)
    return Path("data/rw.sqlite")


def check_doi(doi: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Check whether *doi* appears in Retraction Watch.

    Parameters
    ----------
    doi:
        Raw DOI string (URL prefix and ``doi:`` prefix are stripped internally).
    db_path:
        Path to the SQLite database.  Defaults to the ``RW_DB_PATH``
        environment variable or ``"data/rw.sqlite"``.

    Returns
    -------
    dict
        ``{"query": str, "matched": bool, "matches": list[dict], "meta": dict}``
    """
    path = _resolve_path(db_path)
    conn = get_connection(path)
    matches = query_by_doi(conn, doi)
    meta = get_meta(conn)
    return {"query": doi, "matched": bool(matches), "matches": matches, "meta": meta}


def check_pmid(pmid: int | str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Check whether *pmid* appears in Retraction Watch.

    Parameters
    ----------
    pmid:
        PubMed ID (integer or numeric string).
    db_path:
        Path to the SQLite database.

    Returns
    -------
    dict
        ``{"query": int|str, "matched": bool, "matches": list[dict], "meta": dict}``
    """
    path = _resolve_path(db_path)
    conn = get_connection(path)
    matches = query_by_pmid(conn, pmid)
    meta = get_meta(conn)
    return {"query": pmid, "matched": bool(matches), "matches": matches, "meta": meta}


def check_batch(
    dois: list[str] | None = None,
    pmids: list[int | str] | None = None,
    *,
    db_path: str | Path | None = None,
) -> str:
    """Check a list of DOIs and/or PMIDs against Retraction Watch.

    Parameters
    ----------
    dois:
        List of raw DOI strings.
    pmids:
        List of PubMed IDs (integers or strings).
    db_path:
        Path to the SQLite database.

    Returns
    -------
    str
        JSON string with keys ``results`` (list of per-query dicts) and ``meta``.
        Each result has ``query``, ``query_type``, ``matched``, ``matches``.
    """
    path = _resolve_path(db_path)
    conn = get_connection(path)
    results = query_batch(conn, dois=dois or [], pmids=pmids or [])
    meta = get_meta(conn)
    return json.dumps({"results": results, "meta": meta}, default=str)
