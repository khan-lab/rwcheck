"""Integration tests for the FastAPI application.

The tests use ``httpx.AsyncClient`` with the ASGI transport so no network
traffic is involved.  The sample_db fixture from conftest.py is used and
injected into the app via monkeypatching.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# We import the app module lazily inside tests so we can patch before import.
import rw_api.main as main_module


# ── /api/v1/meta ──────────────────────────────────────────────────────────────


def test_get_meta(client: TestClient) -> None:
    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == "10"
    assert "dataset_version" in data
    assert "built_at" in data


# ── /api/v1/check/doi ─────────────────────────────────────────────────────────


def test_match_doi_hit(client: TestClient) -> None:
    resp = client.get("/api/v1/check/doi/10.9999/jmat.2020.001234")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert len(data["matches"]) == 1
    assert data["matches"][0]["record_id"] == 1001
    assert "meta" in data


def test_match_doi_miss(client: TestClient) -> None:
    resp = client.get("/api/v1/check/doi/10.0000/does.not.exist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is False
    assert data["matches"] == []


def test_match_doi_with_prefix(client: TestClient) -> None:
    """DOI passed with https://doi.org/ is normalised by the server."""
    import urllib.parse

    encoded = urllib.parse.quote("https://doi.org/10.9999/jmat.2020.001234", safe="")
    resp = client.get(f"/api/v1/check/doi/{encoded}")
    assert resp.status_code == 200
    assert resp.json()["matched"] is True


def test_match_doi_with_slashes(client: TestClient) -> None:
    """DOI containing slashes is handled via :path parameter."""
    resp = client.get("/api/v1/check/doi/10.1051/e3sconf/202453804025")
    # This DOI is not in the sample; we just check it does not 404.
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


# ── /api/v1/check/pmid ────────────────────────────────────────────────────────


def test_match_pmid_hit(client: TestClient) -> None:
    resp = client.get("/api/v1/check/pmid/12345678")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert data["matches"][0]["record_id"] == 1001


def test_match_pmid_miss(client: TestClient) -> None:
    resp = client.get("/api/v1/check/pmid/99999999")
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


def test_match_pmid_invalid(client: TestClient) -> None:
    resp = client.get("/api/v1/check/pmid/not-a-number")
    assert resp.status_code == 422


# ── POST /api/v1/check/batch ──────────────────────────────────────────────────


def test_batch_dois(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/check/batch",
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
    resp = client.post("/api/v1/check/batch", json={"dois": [], "pmids": [87654321, 99999999]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2


def test_batch_mixed(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/check/batch",
        json={"dois": ["10.8888/chem.2019.056789"], "pmids": [12345678]},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["matched"] for r in results)


def test_batch_empty(client: TestClient) -> None:
    resp = client.post("/api/v1/check/batch", json={"dois": [], "pmids": []})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_batch_too_large(client: TestClient) -> None:
    dois = [f"10.9999/doi{i}" for i in range(501)]
    resp = client.post("/api/v1/check/batch", json={"dois": dois, "pmids": []})
    assert resp.status_code == 422


# ── /health ───────────────────────────────────────────────────────────────────


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── / (landing page) ──────────────────────────────────────────────────────────
# The landing page is now served by NiceGUI (an SPA), so the GET / response is
# a NiceGUI shell page rather than the old hand-rendered HTML.  We only check
# that the root route responds with 200 and HTML content.


def test_landing_page_returns_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ── /api/v1/stats ─────────────────────────────────────────────────────────────


def test_stats_endpoint_structure(client: TestClient) -> None:
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "total_records",
        "total_journals",
        "total_countries",
        "total_authors",
        "doi_coverage",
        "pmid_coverage",
        "by_year",
        "top_journals",
        "by_country",
        "meta",
    ):
        assert key in data, f"Missing key: {key}"
    assert isinstance(data["by_year"], list)
    assert isinstance(data["top_journals"], list)


def test_stats_total_matches_meta(client: TestClient) -> None:
    stats = client.get("/api/v1/stats").json()
    meta = client.get("/api/v1/meta").json()
    assert str(stats["total_records"]) == meta["row_count"]


# ── GET /api/v1/search ────────────────────────────────────────────────────────


def test_search_requires_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/search")
    assert resp.status_code == 400


def test_search_by_journal_structure(client: TestClient) -> None:
    resp = client.get("/api/v1/search?journal=journal")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total", "limit", "offset", "results", "meta"):
        assert key in data, f"Missing key: {key}"
    assert isinstance(data["results"], list)
    assert data["limit"] == 100
    assert data["offset"] == 0


def test_search_pagination(client: TestClient) -> None:
    resp = client.get("/api/v1/search?journal=journal&limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) <= 2


def test_search_no_results(client: TestClient) -> None:
    resp = client.get("/api/v1/search?journal=zzz_no_match_xyz_abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


def test_search_combined_filters(client: TestClient) -> None:
    resp = client.get("/api/v1/search?journal=journal&country=usa")
    assert resp.status_code == 200
    assert "total" in resp.json()


# ── POST /api/v1/check/bib ────────────────────────────────────────────────────


def test_check_bib_detects_retracted(client: TestClient, tmp_path: Path) -> None:
    bib = tmp_path / "test.bib"
    bib.write_text("@article{smith, doi={10.9999/jmat.2020.001234}}")
    with bib.open("rb") as f:
        resp = client.post("/api/v1/check/bib", files={"file": ("test.bib", f, "text/plain")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["retracted"] == 1
    assert data["results"][0]["matched"] is True


# ── GET /api/v1/enrich/doi ────────────────────────────────────────────────────


async def _null_crossref(doi: str):  # noqa: ARG001
    return None


async def _null_openalex(doi: str, retraction_year):  # noqa: ARG001
    return None


def test_enrich_doi_structure(client: TestClient) -> None:
    """Endpoint returns the expected top-level keys when external APIs are mocked out."""
    with (
        patch.object(main_module, "_fetch_crossref", side_effect=_null_crossref),
        patch.object(main_module, "_fetch_openalex", side_effect=_null_openalex),
    ):
        resp = client.get("/api/v1/enrich/doi/10.9999/jmat.2020.001234")
    assert resp.status_code == 200
    data = resp.json()
    assert "doi" in data
    assert "retraction_status" in data
    assert "crossref" in data
    assert "openalex" in data


def test_enrich_doi_retracted(client: TestClient) -> None:
    """A known-retracted DOI is correctly flagged in retraction_status."""
    with (
        patch.object(main_module, "_fetch_crossref", side_effect=_null_crossref),
        patch.object(main_module, "_fetch_openalex", side_effect=_null_openalex),
    ):
        resp = client.get("/api/v1/enrich/doi/10.9999/jmat.2020.001234")
    assert resp.status_code == 200
    assert resp.json()["retraction_status"]["matched"] is True


def test_enrich_doi_not_found(client: TestClient) -> None:
    """An unknown DOI returns matched=False but still 200."""
    with (
        patch.object(main_module, "_fetch_crossref", side_effect=_null_crossref),
        patch.object(main_module, "_fetch_openalex", side_effect=_null_openalex),
    ):
        resp = client.get("/api/v1/enrich/doi/10.0000/does.not.exist")
    assert resp.status_code == 200
    assert resp.json()["retraction_status"]["matched"] is False


def test_check_bib_non_bib_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/check/bib", files={"file": ("report.txt", b"hello", "text/plain")})
    assert resp.status_code == 422


def test_check_bib_clean_file(client: TestClient, tmp_path: Path) -> None:
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{x, doi={10.0001/clean.doi}}")
    with bib.open("rb") as f:
        resp = client.post("/api/v1/check/bib", files={"file": ("clean.bib", f, "text/plain")})
    assert resp.status_code == 200
    assert resp.json()["retracted"] == 0
