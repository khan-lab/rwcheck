"""Shared pytest fixtures for rwcheck tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture()
def client(sample_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with the DB path patched to the sample fixture.

    Shared across all test modules that need API access.
    """
    import rw_api.main as main_module
    from rwcheck.db import get_connection

    monkeypatch.setattr(main_module, "_DB_PATH", sample_db)
    main_module._cached_meta.cache_clear()
    main_module._cached_stats.cache_clear()

    def _fake_get_conn() -> sqlite3.Connection:
        return get_connection(sample_db)

    monkeypatch.setattr(main_module, "_get_conn", _fake_get_conn)

    with patch.object(main_module, "_update_db_task", return_value=None):
        with TestClient(main_module.app, raise_server_exceptions=True) as c:
            yield c
