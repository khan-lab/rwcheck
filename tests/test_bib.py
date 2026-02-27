"""Tests for BibTeX parsing, report generation, and the batch-bib CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rwcheck.bib import BibEntry, parse_bib_file
from rwcheck.cli import app
from rwcheck.report import BibResult, generate_html_report, generate_json_report, generate_md_report

FIXTURE_BIB = Path(__file__).parent / "fixtures" / "sample.bib"

runner = CliRunner()

# ── Fake meta ─────────────────────────────────────────────────────────────────

_FAKE_META = {
    "dataset_version": "abcd1234abcd1234",
    "built_at": "2024-11-01T12:00:00+00:00",
    "row_count": "10",
    "source_url": "file:///test/sample.csv",
}

# ── parse_bib_file ────────────────────────────────────────────────────────────


def test_parse_bib_file_entry_count() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    assert len(entries) == 13


def test_parse_bib_file_keys() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    keys = {e.key for e in entries}
    assert "smith2020mat" in keys
    assert "unknown_book" in keys
    assert "rodriguezeprint" in keys


def test_parse_bib_doi_field() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    smith = next(e for e in entries if e.key == "smith2020mat")
    assert smith.doi == "10.9999/jmat.2020.001234"
    assert smith.doi_raw == "10.9999/jmat.2020.001234"


def test_extract_doi_from_url_field() -> None:
    """Entry with url = {https://doi.org/10.xxx} should have doi extracted."""
    entries = parse_bib_file(FIXTURE_BIB)
    roe = next(e for e in entries if e.key == "roe2021envurl")
    assert roe.doi == "10.7777/env.2021.climate99"
    assert roe.doi_raw is not None  # raw comes from the URL match


def test_extract_pmid_from_pmid_field() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    sharma = next(e for e in entries if e.key == "sharma2020pmid")
    assert sharma.pmid == 11223344


def test_extract_pmid_from_eprint() -> None:
    """eprint + eprinttype=pubmed should map to pmid."""
    entries = parse_bib_file(FIXTURE_BIB)
    rodriguez = next(e for e in entries if e.key == "rodriguezeprint")
    assert rodriguez.pmid == 87654321
    assert rodriguez.doi is None  # no doi field in that entry


def test_entry_no_doi_or_pmid() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    book = next(e for e in entries if e.key == "unknown_book")
    assert book.doi is None
    assert book.pmid is None
    assert book.checkable is False


def test_entry_with_doi_is_checkable() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    smith = next(e for e in entries if e.key == "smith2020mat")
    assert smith.checkable is True


def test_display_fields() -> None:
    entries = parse_bib_file(FIXTURE_BIB)
    smith = next(e for e in entries if e.key == "smith2020mat")
    assert smith.year == "2020"
    assert smith.journal == "Journal of Materials"
    assert smith.title == "Fabrication of advanced composite materials"


def test_short_author_single() -> None:
    e = BibEntry(key="k", entry_type="article", authors="Tanaka, Yuki")
    assert e.short_author == "Tanaka"


def test_short_author_et_al() -> None:
    e = BibEntry(key="k", entry_type="article", authors="Smith, Alice and Jones, Bob")
    assert e.short_author == "Smith et al."


# ── BibResult ─────────────────────────────────────────────────────────────────


def _make_match(record_id: int = 1001) -> dict:
    return {
        "record_id": record_id,
        "title": "Test paper",
        "journal": "Test Journal",
        "retraction_nature": "Retraction",
        "reason": "Data fabrication",
        "retraction_date": "2022-01-01",
        "retraction_doi": "10.9999/retract",
        "retraction_doi_raw": "10.9999/retract",
        "original_paper_doi": "10.9999/jmat.2020.001234",
        "original_paper_doi_raw": "10.9999/jmat.2020.001234",
        "original_paper_pmid": 12345678,
        "retraction_pmid": None,
        "paywalled": "No",
        "urls": "",
        "country": "USA",
        "publisher": "Test Publisher",
        "author": "Alice Smith",
    }


def test_bibresult_matched() -> None:
    entry = BibEntry(key="k", entry_type="article", doi="10.x/y")
    result = BibResult(entry=entry, doi_matches=[_make_match()], pmid_matches=[])
    assert result.matched is True


def test_bibresult_not_matched() -> None:
    entry = BibEntry(key="k", entry_type="article", doi="10.x/y")
    result = BibResult(entry=entry)
    assert result.matched is False


def test_bibresult_dedup_matches() -> None:
    """Same record_id appearing in both doi and pmid matches → deduplicated."""
    entry = BibEntry(key="k", entry_type="article", doi="10.x/y", pmid=12345678)
    m = _make_match(1001)
    result = BibResult(entry=entry, doi_matches=[m], pmid_matches=[m])
    assert len(result.all_matches) == 1


# ── JSON report ───────────────────────────────────────────────────────────────


def _make_results() -> list[BibResult]:
    entries = parse_bib_file(FIXTURE_BIB)
    results = []
    for e in entries:
        if e.key == "smith2020mat":
            results.append(BibResult(entry=e, doi_matches=[_make_match(1001)]))
        elif e.key == "unknown_book":
            results.append(BibResult(entry=e))  # unchecked
        else:
            results.append(BibResult(entry=e))  # clean
    return results


def test_generate_json_report_structure() -> None:
    results = _make_results()
    raw = generate_json_report(results, _FAKE_META, FIXTURE_BIB)
    data = json.loads(raw)

    assert data["input_file"] == "sample.bib"
    assert "summary" in data
    assert data["summary"]["total"] == 13
    assert data["summary"]["retracted"] == 1
    assert data["summary"]["unchecked"] == 1
    assert data["summary"]["clean"] == 11
    assert len(data["results"]) == 13


def test_generate_json_report_matched_entry() -> None:
    results = _make_results()
    raw = generate_json_report(results, _FAKE_META, FIXTURE_BIB)
    data = json.loads(raw)
    smith = next(r for r in data["results"] if r["key"] == "smith2020mat")
    assert smith["matched"] is True
    assert len(smith["matches"]) == 1


# ── Markdown report ────────────────────────────────────────────────────────────


def test_generate_md_report_sections() -> None:
    results = _make_results()
    md = generate_md_report(results, _FAKE_META, FIXTURE_BIB)

    assert "# Retraction Watch Report" in md
    assert "## Summary" in md
    assert "⚠️ Retracted" in md
    assert "✓ Clean" in md
    assert "⬜ Unchecked" in md


def test_generate_md_report_retracted_entry() -> None:
    results = _make_results()
    md = generate_md_report(results, _FAKE_META, FIXTURE_BIB)
    assert "smith2020mat" in md
    assert "Retraction" in md


def test_generate_md_report_summary_counts() -> None:
    results = _make_results()
    md = generate_md_report(results, _FAKE_META, FIXTURE_BIB)
    assert "| Total references | **13** |" in md
    assert "| ⚠️ Retracted | **1** |" in md


# ── HTML report ───────────────────────────────────────────────────────────────


def test_generate_html_report_is_valid_html() -> None:
    """HTML report must include basic structural elements and summary counts."""
    results = _make_results()
    html_str = generate_html_report(results, _FAKE_META, FIXTURE_BIB)

    assert "<!DOCTYPE html>" in html_str
    assert "<html" in html_str
    assert "</html>" in html_str
    assert "<body" in html_str
    assert "<title>" in html_str
    assert "sample.bib" in html_str
    # Summary counts present.
    assert ">13<" in html_str  # total
    assert ">1<" in html_str   # retracted
    assert ">11<" in html_str  # clean
    assert ">1<" in html_str   # unchecked
    # Sections present.
    assert "Retracted References" in html_str
    assert "Clean References" in html_str
    assert "Unchecked" in html_str


def test_generate_html_report_retracted_entry() -> None:
    """Retracted entry key, DOI, and DOI link must appear in the HTML."""
    results = _make_results()
    html_str = generate_html_report(results, _FAKE_META, FIXTURE_BIB)

    # Retracted entry key appears.
    assert "smith2020mat" in html_str
    # DOI appears as a link to doi.org.
    assert "https://doi.org/10.9999/jmat.2020.001234" in html_str
    # RETRACTED badge present.
    assert "RETRACTED" in html_str
    # Retraction nature shown.
    assert "Retraction" in html_str


def test_generate_html_report_no_external_resources() -> None:
    """Self-contained: no http(s) links in <head> (CDN, stylesheets, scripts)."""
    results = _make_results()
    html_str = generate_html_report(results, _FAKE_META, FIXTURE_BIB)

    head_end = html_str.index("</head>")
    head = html_str[:head_end]
    # No external URLs in the <head>.
    assert "http" not in head


# ── batch-bib CLI command ─────────────────────────────────────────────────────


def test_batch_bib_cli_hit(sample_db: Path, tmp_path: Path) -> None:
    """batch-bib should find retracted entries and write report files."""
    result = runner.invoke(
        app,
        ["batch-bib", str(FIXTURE_BIB), "--db", str(sample_db), "--report-dir", str(tmp_path)],
    )
    assert result.exit_code == 0

    # At least one retraction found.
    assert "Retracted" in result.output or "RETRACTED" in result.output or "✗" in result.output

    # All three report files created.
    json_report = tmp_path / "sample_rwcheck.json"
    md_report = tmp_path / "sample_rwcheck.md"
    html_report = tmp_path / "sample_rwcheck.html"
    assert json_report.exists(), "JSON report file was not created"
    assert md_report.exists(), "Markdown report file was not created"
    assert html_report.exists(), "HTML report file was not created"

    # JSON is valid and has correct structure.
    data = json.loads(json_report.read_text())
    assert data["summary"]["total"] == 13
    assert data["summary"]["retracted"] >= 1


def test_batch_bib_cli_reports_in_default_dir(sample_db: Path, tmp_path: Path) -> None:
    """Without --report-dir, reports go next to the .bib file."""
    import shutil

    bib_copy = tmp_path / "sample.bib"
    shutil.copy(FIXTURE_BIB, bib_copy)

    result = runner.invoke(app, ["batch-bib", str(bib_copy), "--db", str(sample_db)])
    assert result.exit_code == 0

    assert (tmp_path / "sample_rwcheck.json").exists()
    assert (tmp_path / "sample_rwcheck.md").exists()
    assert (tmp_path / "sample_rwcheck.html").exists()


def test_batch_bib_cli_missing_file(sample_db: Path) -> None:
    result = runner.invoke(
        app, ["batch-bib", "/nonexistent/refs.bib", "--db", str(sample_db)]
    )
    assert result.exit_code != 0
