"""SQLite schema definition and migration."""

import sqlite3

from stock_db.paths import STOCKS_DB_PATH

DB_PATH = STOCKS_DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a connection to the stock database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a table, or empty set if table doesn't exist."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    return {row[1] for row in rows}


def init_db() -> None:
    """Initialize the database connection.

    Note: Tables are created by stock_db. This is a no-op for formula_screening.
    """
    # Tables are managed by stock_db
    pass
