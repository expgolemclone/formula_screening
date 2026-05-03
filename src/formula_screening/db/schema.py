"""SQLite connection delegation to stock_db."""

from __future__ import annotations

import sqlite3

from stock_db.paths import STOCKS_DB_PATH
from stock_db.storage.connection import get_connection as _stock_db_get_connection

DB_PATH = STOCKS_DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a connection to the stock database via stock_db API."""
    return _stock_db_get_connection(DB_PATH)


def init_db() -> None:
    """Initialize the database connection.

    Note: Tables are created by stock_db. This is a no-op for formula_screening.
    """
    # Tables are managed by stock_db
    pass
