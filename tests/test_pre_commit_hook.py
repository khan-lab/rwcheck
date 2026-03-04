"""Tests for the rwcheck pre-commit hook entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from rwcheck.pre_commit_hook import main


@pytest.fixture()
def _db_env(sample_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point RW_DB_PATH at the sample fixture DB."""
    monkeypatch.setenv("RW_DB_PATH", str(sample_db))


def test_hook_clean_bib(tmp_path: Path, _db_env: None) -> None:
    """A .bib file with no retracted DOIs should exit 0."""
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{jones, doi={10.0000/totally.unknown}}")
    assert main([str(bib)]) == 0


def test_hook_retracted_bib(tmp_path: Path, _db_env: None) -> None:
    """A .bib file containing a retracted DOI should exit 1."""
    bib = tmp_path / "bad.bib"
    bib.write_text("@article{smith, doi={10.9999/jmat.2020.001234}}")
    assert main([str(bib)]) == 1


def test_hook_no_files(_db_env: None) -> None:
    """Called with no arguments should exit 0 (nothing to check)."""
    assert main([]) == 0


def test_hook_missing_file(tmp_path: Path, _db_env: None) -> None:
    """Non-existent file path should be skipped silently and exit 0."""
    assert main([str(tmp_path / "ghost.bib")]) == 0
