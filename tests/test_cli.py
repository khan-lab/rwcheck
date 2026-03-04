"""Integration tests for the rwcheck CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rwcheck.cli import app

runner = CliRunner()
FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample.csv"


# ── doi command ───────────────────────────────────────────────────────────────


def test_doi_hit(sample_db: Path) -> None:
    result = runner.invoke(app, ["doi", "10.9999/jmat.2020.001234", "--db", str(sample_db)])
    assert result.exit_code == 0
    assert "RETRACTED" in result.output


def test_doi_miss(sample_db: Path) -> None:
    result = runner.invoke(app, ["doi", "10.0000/does.not.exist", "--db", str(sample_db)])
    assert result.exit_code == 0
    assert "NOT FOUND" in result.output


def test_doi_hit_json(sample_db: Path) -> None:
    result = runner.invoke(
        app, ["doi", "10.9999/jmat.2020.001234", "--db", str(sample_db), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["matched"] is True
    assert data["matches"][0]["record_id"] == 1001


def test_doi_with_prefix(sample_db: Path) -> None:
    """DOI with https://doi.org/ prefix is normalised before lookup."""
    result = runner.invoke(
        app,
        ["doi", "https://doi.org/10.8888/chem.2019.056789", "--db", str(sample_db)],
    )
    assert result.exit_code == 0
    assert "RETRACTED" in result.output


def test_doi_missing_db() -> None:
    result = runner.invoke(app, ["doi", "10.9999/x", "--db", "/nonexistent/rw.sqlite"])
    assert result.exit_code != 0


# ── pmid command ──────────────────────────────────────────────────────────────


def test_pmid_hit(sample_db: Path) -> None:
    result = runner.invoke(app, ["pmid", "12345678", "--db", str(sample_db)])
    assert result.exit_code == 0
    assert "RETRACTED" in result.output


def test_pmid_miss(sample_db: Path) -> None:
    result = runner.invoke(app, ["pmid", "99999999", "--db", str(sample_db)])
    assert result.exit_code == 0
    assert "NOT FOUND" in result.output


def test_pmid_invalid(sample_db: Path) -> None:
    result = runner.invoke(app, ["pmid", "not-a-number", "--db", str(sample_db)])
    assert result.exit_code != 0


# ── batch-doi command ─────────────────────────────────────────────────────────


def test_batch_doi_text_file(sample_db: Path, tmp_path: Path) -> None:
    doi_file = tmp_path / "dois.txt"
    doi_file.write_text(
        "10.9999/jmat.2020.001234\n10.8888/chem.2019.056789\n10.0000/does.not.exist\n"
    )
    result = runner.invoke(app, ["batch-doi", str(doi_file), "--db", str(sample_db)])
    assert result.exit_code == 0
    assert "2/3" in result.output  # 2 retracted out of 3


def test_batch_doi_json_out(sample_db: Path, tmp_path: Path) -> None:
    doi_file = tmp_path / "dois.txt"
    doi_file.write_text("10.9999/jmat.2020.001234\n10.0000/nope\n")
    result = runner.invoke(
        app, ["batch-doi", str(doi_file), "--db", str(sample_db), "--out", "json"]
    )
    assert result.exit_code == 0
    # Strip any stray status lines before the JSON block
    json_text = result.output[result.output.index("{") :]
    data = json.loads(json_text)
    assert len(data["results"]) == 2


def test_batch_doi_tsv_out(sample_db: Path, tmp_path: Path) -> None:
    doi_file = tmp_path / "dois.txt"
    doi_file.write_text("10.9999/jmat.2020.001234\n")
    result = runner.invoke(
        app, ["batch-doi", str(doi_file), "--db", str(sample_db), "--out", "tsv"]
    )
    assert result.exit_code == 0
    # tsv output: header + 1 data row; strip any stray status lines
    lines = [ln for ln in result.output.strip().splitlines() if not ln.startswith("Checking")]
    assert len(lines) == 2  # header + 1 data row
    assert "True" in lines[1]


def test_batch_doi_csv_file(sample_db: Path, tmp_path: Path) -> None:
    """batch-doi reads from a CSV file using --col to pick the right column."""
    csv_file = tmp_path / "papers.csv"
    csv_file.write_text("doi,title\n10.5555/natgen.2017.diabetesGWAS,Some paper\n")
    result = runner.invoke(
        app,
        ["batch-doi", str(csv_file), "--db", str(sample_db), "--col", "doi"],
    )
    assert result.exit_code == 0
    assert "RETRACTED" in result.output


def test_batch_doi_missing_file(sample_db: Path) -> None:
    result = runner.invoke(app, ["batch-doi", "/nonexistent/file.txt", "--db", str(sample_db)])
    assert result.exit_code != 0


# ── version flag ──────────────────────────────────────────────────────────────


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "rwcheck" in result.output
