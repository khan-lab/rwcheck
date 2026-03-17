"""Tests for RIS parsing and the ris CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rwcheck.cli import app
from rwcheck.ris import parse_ris_file

FIXTURE_RIS = Path(__file__).parent / "fixtures" / "sample.ris"

runner = CliRunner()

# ── parse_ris_file ─────────────────────────────────────────────────────────────


def test_parse_ris_file_entry_count() -> None:
    entries = parse_ris_file(FIXTURE_RIS)
    assert len(entries) == 5


def test_parse_ris_doi_extraction() -> None:
    """Entry with DO tag has its DOI normalised correctly."""
    entries = parse_ris_file(FIXTURE_RIS)
    smith = next(e for e in entries if "Smith" in (e.authors or ""))
    assert smith.doi == "10.9999/jmat.2020.001234"
    assert smith.doi_raw is not None


def test_parse_ris_pmid_extraction() -> None:
    """Entry with AN tag has its PMID parsed as integer."""
    entries = parse_ris_file(FIXTURE_RIS)
    jones = next(e for e in entries if "Jones" in (e.authors or ""))
    assert jones.pmid == 12345678


def test_parse_ris_doi_from_url() -> None:
    """Entry whose UR tag contains a doi.org URL has DOI extracted."""
    entries = parse_ris_file(FIXTURE_RIS)
    brown = next(e for e in entries if "Brown" in (e.authors or ""))
    assert brown.doi == "10.8888/chem.2019.056789"


def test_parse_ris_checkable_true() -> None:
    entries = parse_ris_file(FIXTURE_RIS)
    smith = next(e for e in entries if "Smith" in (e.authors or ""))
    assert smith.checkable is True


def test_parse_ris_unchecked_entry() -> None:
    """Book entry with no DOI or PMID is not checkable."""
    entries = parse_ris_file(FIXTURE_RIS)
    book = next(e for e in entries if e.entry_type == "BOOK")
    assert book.doi is None
    assert book.pmid is None
    assert book.checkable is False


def test_parse_ris_clean_entry() -> None:
    """Entry with a DOI not in the database is checkable but not retracted."""
    entries = parse_ris_file(FIXTURE_RIS)
    lee = next(e for e in entries if "Lee" in (e.authors or ""))
    assert lee.doi == "10.0000/clean.2021.999999"
    assert lee.checkable is True


def test_parse_ris_year_and_title() -> None:
    entries = parse_ris_file(FIXTURE_RIS)
    smith = next(e for e in entries if "Smith" in (e.authors or ""))
    assert smith.year == "2020"
    assert smith.title is not None
    assert smith.journal is not None


def test_parse_ris_key_synthesised() -> None:
    """Keys are synthesised from author surname + year when no ID tag is present."""
    entries = parse_ris_file(FIXTURE_RIS)
    for entry in entries:
        assert entry.key  # all entries must have a non-empty key


# ── ris CLI command ────────────────────────────────────────────────────────────


def test_ris_cli_hit(sample_db: Path, tmp_path: Path) -> None:
    """ris command should find retracted entries and write report files."""
    result = runner.invoke(
        app,
        ["ris", str(FIXTURE_RIS), "--db", str(sample_db), "--report-dir", str(tmp_path)],
    )
    assert result.exit_code == 0

    # At least one retraction found.
    assert "Retracted" in result.output or "✗" in result.output

    # All three report files created.
    json_report = tmp_path / "sample_rwcheck.json"
    md_report = tmp_path / "sample_rwcheck.md"
    html_report = tmp_path / "sample_rwcheck.html"
    assert json_report.exists(), "JSON report was not created"
    assert md_report.exists(), "Markdown report was not created"
    assert html_report.exists(), "HTML report was not created"

    # JSON contains correct counts.
    data = json.loads(json_report.read_text())
    assert data["summary"]["total"] == 5
    assert data["summary"]["retracted"] >= 1
    assert data["summary"]["unchecked"] == 1  # the book entry


def test_ris_cli_reports_in_default_dir(sample_db: Path, tmp_path: Path) -> None:
    """Without --report-dir, reports go next to the .ris file."""
    import shutil

    ris_copy = tmp_path / "sample.ris"
    shutil.copy(FIXTURE_RIS, ris_copy)

    result = runner.invoke(app, ["ris", str(ris_copy), "--db", str(sample_db)])
    assert result.exit_code == 0

    assert (tmp_path / "sample_rwcheck.json").exists()
    assert (tmp_path / "sample_rwcheck.md").exists()
    assert (tmp_path / "sample_rwcheck.html").exists()


def test_ris_cli_missing_file(sample_db: Path) -> None:
    result = runner.invoke(app, ["ris", "/nonexistent/refs.ris", "--db", str(sample_db)])
    assert result.exit_code != 0


# ── POST /api/v1/check/ris ────────────────────────────────────────────────────


def test_check_ris_detects_retracted(client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with FIXTURE_RIS.open("rb") as f:
        resp = client.post("/api/v1/check/ris", files={"file": ("sample.ris", f, "text/plain")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["retracted"] >= 1
    assert data["total"] == 5


def test_check_ris_non_ris_rejected(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/api/v1/check/ris", files={"file": ("report.bib", b"@article{}", "text/plain")})
    assert resp.status_code == 422


def test_check_ris_clean_entry(client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ris_content = b"TY  - JOUR\nDO  - 10.0001/no.retraction\nER  -\n"
    resp = client.post(
        "/api/v1/check/ris", files={"file": ("clean.ris", ris_content, "text/plain")}
    )
    assert resp.status_code == 200
    assert resp.json()["retracted"] == 0
