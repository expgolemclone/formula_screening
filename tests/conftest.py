"""Shared test fixtures."""

import sqlite3

import pytest

from formula_screening.db.schema import _SCHEMA_SQL


@pytest.fixture()
def conn():
    """In-memory SQLite connection with schema applied."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQL)
    yield c
    c.close()
