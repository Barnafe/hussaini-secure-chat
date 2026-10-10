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
