"""Integration tests for the FastAPI application.

The tests use ``httpx.AsyncClient`` with the ASGI transport so no network
traffic is involved.  The sample_db fixture from conftest.py is used and
injected into the app via monkeypatching.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# We import the app module lazily inside tests so we can patch before import.
import rw_api.main as main_module
from rwcheck.db import get_connection


@pytest.fixture()
def client(sample_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with the DB path patched to the sample fixture."""
    monkeypatch.setattr(main_module, "_DB_PATH", sample_db)

    # Clear any cached meta/stats from previous test runs.
    main_module._cached_meta.cache_clear()
    main_module._cached_stats.cache_clear()

    # Override _get_conn to always use the sample_db.
    def _fake_get_conn() -> sqlite3.Connection:
        return get_connection(sample_db)

    monkeypatch.setattr(main_module, "_get_conn", _fake_get_conn)

    # Disable the background scheduler / initial update thread during tests.
    with patch.object(main_module, "_update_db_task", return_value=None):
        with TestClient(main_module.app, raise_server_exceptions=True) as c:
            yield c


# ── /meta ─────────────────────────────────────────────────────────────────────

def test_get_meta(client: TestClient) -> None:
    resp = client.get("/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == "10"
    assert "dataset_version" in data
    assert "built_at" in data


# ── /check/doi ────────────────────────────────────────────────────────────────

def test_match_doi_hit(client: TestClient) -> None:
    resp = client.get("/check/doi/10.9999/jmat.2020.001234")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert len(data["matches"]) == 1
    assert data["matches"][0]["record_id"] == 1001
    assert "meta" in data


def test_match_doi_miss(client: TestClient) -> None:
    resp = client.get("/check/doi/10.0000/does.not.exist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is False
    assert data["matches"] == []


def test_match_doi_with_prefix(client: TestClient) -> None:
    """DOI passed with https://doi.org/ is normalised by the server."""
    import urllib.parse

    encoded = urllib.parse.quote("https://doi.org/10.9999/jmat.2020.001234", safe="")
    resp = client.get(f"/check/doi/{encoded}")
    assert resp.status_code == 200
    assert resp.json()["matched"] is True


def test_match_doi_with_slashes(client: TestClient) -> None:
    """DOI containing slashes is handled via :path parameter."""
    resp = client.get("/check/doi/10.1051/e3sconf/202453804025")
    # This DOI is not in the sample; we just check it does not 404.
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


# ── /check/pmid ───────────────────────────────────────────────────────────────

def test_match_pmid_hit(client: TestClient) -> None:
    resp = client.get("/check/pmid/12345678")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert data["matches"][0]["record_id"] == 1001


def test_match_pmid_miss(client: TestClient) -> None:
    resp = client.get("/check/pmid/99999999")
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


def test_match_pmid_invalid(client: TestClient) -> None:
    resp = client.get("/check/pmid/not-a-number")
    assert resp.status_code == 422


# ── POST /check/batch ─────────────────────────────────────────────────────────

def test_batch_dois(client: TestClient) -> None:
    resp = client.post(
        "/check/batch",
        json={"dois": ["10.9999/jmat.2020.001234", "10.0000/nope"], "pmids": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    matched = [r for r in data["results"] if r["matched"]]
    not_matched = [r for r in data["results"] if not r["matched"]]
    assert len(matched) == 1
    assert len(not_matched) == 1


def test_batch_pmids(client: TestClient) -> None:
    resp = client.post("/check/batch", json={"dois": [], "pmids": [87654321, 99999999]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2


def test_batch_mixed(client: TestClient) -> None:
    resp = client.post(
        "/check/batch",
        json={"dois": ["10.8888/chem.2019.056789"], "pmids": [12345678]},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["matched"] for r in results)


def test_batch_empty(client: TestClient) -> None:
    resp = client.post("/check/batch", json={"dois": [], "pmids": []})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_batch_too_large(client: TestClient) -> None:
    dois = [f"10.9999/doi{i}" for i in range(501)]
    resp = client.post("/check/batch", json={"dois": dois, "pmids": []})
    assert resp.status_code == 422


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── / (landing page) ──────────────────────────────────────────────────────────

def test_landing_page_returns_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<!DOCTYPE html>" in resp.text
    assert "rwcheck" in resp.text


def test_landing_page_has_key_sections(client: TestClient) -> None:
    resp = client.get("/")
    html = resp.text
    assert "yearChart" in html           # chart canvas present
    assert "countryMap" in html          # choropleth div present
    assert "Total Records" in html
    assert "Retraction Watch" in html
    assert "How to Cite" in html
    assert "doi-input" in html           # live checker widget
    assert "bib-input" in html           # bib upload widget present


# ── /stats ────────────────────────────────────────────────────────────────────

def test_stats_endpoint_structure(client: TestClient) -> None:
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "total_records", "total_journals", "total_countries", "total_authors",
        "doi_coverage", "pmid_coverage", "by_year", "top_journals",
        "by_country", "meta",
    ):
        assert key in data, f"Missing key: {key}"
    assert isinstance(data["by_year"], list)
    assert isinstance(data["top_journals"], list)


def test_stats_total_matches_meta(client: TestClient) -> None:
    stats = client.get("/stats").json()
    meta  = client.get("/meta").json()
    assert str(stats["total_records"]) == meta["row_count"]


# ── POST /check/bib ───────────────────────────────────────────────────────────

def test_check_bib_detects_retracted(client: TestClient, tmp_path: Path) -> None:
    bib = tmp_path / "test.bib"
    bib.write_text("@article{smith, doi={10.9999/jmat.2020.001234}}")
    with bib.open("rb") as f:
        resp = client.post("/check/bib", files={"file": ("test.bib", f, "text/plain")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["retracted"] == 1
    assert data["results"][0]["matched"] is True


def test_check_bib_non_bib_rejected(client: TestClient) -> None:
    resp = client.post("/check/bib",
                       files={"file": ("report.txt", b"hello", "text/plain")})
    assert resp.status_code == 422


def test_check_bib_clean_file(client: TestClient, tmp_path: Path) -> None:
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{x, doi={10.0001/clean.doi}}")
    with bib.open("rb") as f:
        resp = client.post("/check/bib", files={"file": ("clean.bib", f, "text/plain")})
    assert resp.status_code == 200
    assert resp.json()["retracted"] == 0
