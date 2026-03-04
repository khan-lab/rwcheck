"""Unit tests for rwcheck.db — queries against the sample fixture DB."""

from __future__ import annotations

from pathlib import Path

import pytest

from rwcheck.db import get_connection, get_meta, query_batch, query_by_doi, query_by_pmid


def _conn(sample_db: Path):
    return get_connection(sample_db)


# ── meta ──────────────────────────────────────────────────────────────────────


def test_meta_keys(sample_db: Path) -> None:
    conn = _conn(sample_db)
    meta = get_meta(conn)
    assert "dataset_version" in meta
    assert "built_at" in meta
    assert "row_count" in meta
    assert meta["row_count"] == "10"


# ── DOI lookups ───────────────────────────────────────────────────────────────


def test_doi_hit_original_paper(sample_db: Path) -> None:
    """Lookup by original-paper DOI returns a match."""
    conn = _conn(sample_db)
    results = query_by_doi(conn, "10.9999/jmat.2020.001234")
    assert len(results) == 1
    assert results[0]["record_id"] == 1001


def test_doi_hit_with_prefix(sample_db: Path) -> None:
    """DOI with https://doi.org/ prefix is normalised and still found."""
    conn = _conn(sample_db)
    results = query_by_doi(conn, "https://doi.org/10.9999/jmat.2020.001234")
    assert len(results) == 1


def test_doi_hit_retraction_notice(sample_db: Path) -> None:
    """Lookup by retraction-notice DOI also surfaces the record."""
    conn = _conn(sample_db)
    results = query_by_doi(conn, "10.9999/jmat.2022.retract")
    assert len(results) == 1
    assert results[0]["record_id"] == 1001


def test_doi_miss(sample_db: Path) -> None:
    """A DOI not in the dataset returns an empty list."""
    conn = _conn(sample_db)
    results = query_by_doi(conn, "10.0000/does.not.exist")
    assert results == []


def test_doi_empty_string(sample_db: Path) -> None:
    conn = _conn(sample_db)
    assert query_by_doi(conn, "") == []


def test_doi_uppercase(sample_db: Path) -> None:
    """DOI is case-insensitive."""
    conn = _conn(sample_db)
    results = query_by_doi(conn, "10.8888/CHEM.2019.056789")
    assert len(results) == 1
    assert results[0]["record_id"] == 1002


# ── PMID lookups ──────────────────────────────────────────────────────────────


def test_pmid_hit_original(sample_db: Path) -> None:
    conn = _conn(sample_db)
    results = query_by_pmid(conn, 12345678)
    assert len(results) == 1
    assert results[0]["record_id"] == 1001


def test_pmid_hit_retraction(sample_db: Path) -> None:
    """Record 1003 has a retraction PMID of 33445566."""
    conn = _conn(sample_db)
    results = query_by_pmid(conn, 33445566)
    assert len(results) == 1
    assert results[0]["record_id"] == 1003


def test_pmid_miss(sample_db: Path) -> None:
    conn = _conn(sample_db)
    assert query_by_pmid(conn, 99999999) == []


def test_pmid_zero(sample_db: Path) -> None:
    """PMID 0 is treated as absent."""
    conn = _conn(sample_db)
    assert query_by_pmid(conn, 0) == []


# ── Batch ─────────────────────────────────────────────────────────────────────


def test_batch_mixed(sample_db: Path) -> None:
    conn = _conn(sample_db)
    results = query_batch(
        conn,
        dois=["10.9999/jmat.2020.001234", "10.0000/not-found"],
        pmids=[87654321],
    )
    assert len(results) == 3

    doi_hits = [r for r in results if r["query"] == "10.9999/jmat.2020.001234"]
    assert doi_hits[0]["matched"] is True

    doi_miss = [r for r in results if r["query"] == "10.0000/not-found"]
    assert doi_miss[0]["matched"] is False

    pmid_hit = [r for r in results if r["query_type"] == "pmid"]
    assert pmid_hit[0]["matched"] is True


def test_batch_empty(sample_db: Path) -> None:
    conn = _conn(sample_db)
    assert query_batch(conn, dois=[], pmids=[]) == []


# ── DB not found ──────────────────────────────────────────────────────────────


def test_get_connection_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Database not found"):
        get_connection(tmp_path / "nonexistent.sqlite")
