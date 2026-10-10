"""Shared pytest fixtures."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    database = Database(sqlite_path=str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _private_client_prefs(tmp_path, monkeypatch):
    """Never let a test write client_prefs.json into the project folder."""
    monkeypatch.setenv("HUSSAINI_PREFS_FILE", str(tmp_path / "client_prefs.json"))
