"""Tests for the high-level Python API (rwcheck.api)."""

from __future__ import annotations

import json
from pathlib import Path

from rwcheck.api import check_batch, check_doi, check_pmid


def test_check_doi_hit(sample_db: Path) -> None:
    result = check_doi("10.9999/jmat.2020.001234", db_path=sample_db)
    assert result["matched"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["record_id"] == 1001
    assert "meta" in result


def test_check_doi_miss(sample_db: Path) -> None:
    result = check_doi("10.0000/does.not.exist", db_path=sample_db)
    assert result["matched"] is False
    assert result["matches"] == []


def test_check_doi_normalises_url_prefix(sample_db: Path) -> None:
    result = check_doi("https://doi.org/10.9999/jmat.2020.001234", db_path=sample_db)
    assert result["matched"] is True


def test_check_pmid_hit(sample_db: Path) -> None:
    result = check_pmid(12345678, db_path=sample_db)
    assert result["matched"] is True
    assert result["matches"][0]["record_id"] == 1001


def test_check_pmid_miss(sample_db: Path) -> None:
    result = check_pmid(99999999, db_path=sample_db)
    assert result["matched"] is False


def test_check_pmid_string_input(sample_db: Path) -> None:
    result = check_pmid("12345678", db_path=sample_db)
    assert result["matched"] is True


def test_check_batch_returns_json_string(sample_db: Path) -> None:
    raw = check_batch(dois=["10.9999/jmat.2020.001234", "10.0000/nope"], db_path=sample_db)
    assert isinstance(raw, str)
    data = json.loads(raw)
    assert "results" in data
    assert "meta" in data
    assert len(data["results"]) == 2


def test_check_batch_mixed(sample_db: Path) -> None:
    raw = check_batch(
        dois=["10.8888/chem.2019.056789"],
        pmids=[12345678],
        db_path=sample_db,
    )
    data = json.loads(raw)
    assert all(r["matched"] for r in data["results"])


def test_check_batch_empty(sample_db: Path) -> None:
    raw = check_batch(db_path=sample_db)
    data = json.loads(raw)
    assert data["results"] == []


def test_top_level_import() -> None:
    """Ensure functions are accessible directly from the rwcheck package."""
    import rwcheck

    assert callable(rwcheck.check_doi)
    assert callable(rwcheck.check_pmid)
    assert callable(rwcheck.check_batch)
