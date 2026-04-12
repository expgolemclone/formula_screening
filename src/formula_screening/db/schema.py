"""SQLite schema definition and migration."""

import sqlite3

from formula_screening.config import DB_PATH, ensure_dirs

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker             TEXT PRIMARY KEY,
    edinet_code        TEXT,
    name               TEXT,
    sector             TEXT,
    market             TEXT,
    shares_outstanding INTEGER,
    shares_updated_at  TEXT,
    updated_at         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_edinet_code
    ON stocks (edinet_code) WHERE edinet_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS financial_items (
    ticker     TEXT    NOT NULL,
    period     TEXT    NOT NULL,
    statement  TEXT    NOT NULL,
    item_name  TEXT    NOT NULL,
    value      REAL,
    source     TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (ticker, period, statement, item_name)
);

CREATE INDEX IF NOT EXISTS idx_fi_statement_item
    ON financial_items (statement, item_name);

CREATE INDEX IF NOT EXISTS idx_fi_ticker
    ON financial_items (ticker);

CREATE TABLE IF NOT EXISTS prices (
    ticker     TEXT    NOT NULL,
    date       TEXT    NOT NULL,
    close      REAL,
    volume     INTEGER,
    updated_at TEXT,
    PRIMARY KEY (ticker, date)
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a connection to the application database."""
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a table, or empty set if table doesn't exist."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    return {row[1] for row in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations for existing databases."""
    stock_cols = _table_columns(conn, "stocks")
    if not stock_cols:
        return  # table does not exist yet; _SCHEMA_SQL will create it
    if "edinet_code" not in stock_cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN edinet_code TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_edinet_code "
            "ON stocks (edinet_code) WHERE edinet_code IS NOT NULL"
        )
        conn.commit()
    if "shares_outstanding" not in stock_cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN shares_outstanding INTEGER")
        conn.execute("ALTER TABLE stocks ADD COLUMN shares_updated_at TEXT")
        conn.commit()

    price_cols = _table_columns(conn, "prices")
    if price_cols and "updated_at" not in price_cols:
        conn.execute("ALTER TABLE prices ADD COLUMN updated_at TEXT")
        conn.commit()


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    conn = get_connection()
    try:
        _migrate(conn)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
