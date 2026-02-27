"""Shared pytest fixtures for rwcheck tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.build_db import _DDL, _ingest, _sha256_file

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample.csv"


@pytest.fixture(scope="session")
def sample_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a temporary SQLite DB from the sample fixture CSV.

    Scoped to the session so it is only built once per test run.
    """
    db_path = tmp_path_factory.mktemp("db") / "test_rw.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DDL)
    sha = _sha256_file(FIXTURE_CSV)
    _ingest(conn, FIXTURE_CSV, source_url=str(FIXTURE_CSV), sha256=sha)
    conn.close()
    return db_path
