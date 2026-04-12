"""SQLite schema definition and migration — delegates to stock_db."""

from __future__ import annotations

import sqlite3

from stock_db.db.connection import get_connection as _get_connection
from stock_db.db.schema import _SCHEMA_SQL, init_db as _init_db_on_conn

from formula_screening.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    return _get_connection(DB_PATH)


def init_db() -> None:
    conn = get_connection()
    try:
        _init_db_on_conn(conn)
    finally:
        conn.close()
