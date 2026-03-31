"""Data access layer for stocks, financial_items, and prices."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# stocks
# ---------------------------------------------------------------------------

def upsert_stock(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
    sector: str,
    market: str,
    *,
    edinet_code: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO stocks (ticker, edinet_code, name, sector, market, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            edinet_code=excluded.edinet_code,
            name=excluded.name,
            sector=excluded.sector,
            market=excluded.market,
            updated_at=excluded.updated_at
        """,
        (ticker, edinet_code, name, sector, market, _now()),
    )


def get_all_tickers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT ticker FROM stocks ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]


def get_edinet_code(conn: sqlite3.Connection, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT edinet_code FROM stocks WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row["edinet_code"] if row else None


def get_ticker_edinet_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {ticker: edinet_code} for all stocks with an edinet_code."""
    rows = conn.execute(
        "SELECT ticker, edinet_code FROM stocks WHERE edinet_code IS NOT NULL"
    ).fetchall()
    return {r["ticker"]: r["edinet_code"] for r in rows}


# ---------------------------------------------------------------------------
# financial_items (EAV)
# ---------------------------------------------------------------------------

def upsert_financial_item(
    conn: sqlite3.Connection,
    ticker: str,
    period: str,
    statement: str,
    item_name: str,
    value: float | None,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO financial_items
            (ticker, period, statement, item_name, value, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, period, statement, item_name) DO UPDATE SET
            value=excluded.value,
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        (ticker, period, statement, item_name, value, source, _now()),
    )


def upsert_financial_items_bulk(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> None:
    """Bulk upsert financial items.

    Each dict must contain: ticker, period, statement, item_name, value, source.
    """
    now = _now()
    conn.executemany(
        """
        INSERT INTO financial_items
            (ticker, period, statement, item_name, value, source, updated_at)
        VALUES (:ticker, :period, :statement, :item_name, :value, :source, :updated_at)
        ON CONFLICT(ticker, period, statement, item_name) DO UPDATE SET
            value=excluded.value,
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        [{**r, "updated_at": now} for r in rows],
    )


def get_financial_dict(
    conn: sqlite3.Connection,
    ticker: str,
    period: str | None = None,
) -> dict[str, dict[str, float | None]]:
    """Load financial data as nested dict: {statement: {item_name: value}}.

    If period is None, uses the latest period available.
    """
    if period is None:
        row = conn.execute(
            """
            SELECT period FROM financial_items
            WHERE ticker = ? AND statement = 'pl'
            ORDER BY period DESC LIMIT 1
            """,
            (ticker,),
        ).fetchone()
        if row is None:
            return {}
        period = row["period"]

    rows = conn.execute(
        """
        SELECT statement, item_name, value
        FROM financial_items
        WHERE ticker = ? AND period = ?
        """,
        (ticker, period),
    ).fetchall()

    result: dict[str, dict[str, float | None]] = {}
    for r in rows:
        stmt = r["statement"]
        result.setdefault(stmt, {})[r["item_name"]] = r["value"]

    # Fetch the latest forecast data (separate period from actuals)
    forecast_rows = conn.execute(
        """
        SELECT item_name, value FROM financial_items
        WHERE ticker = ? AND statement = 'forecast'
          AND period = (
              SELECT MAX(period) FROM financial_items
              WHERE ticker = ? AND statement = 'forecast'
          )
        """,
        (ticker, ticker),
    ).fetchall()
    if forecast_rows:
        result["forecast"] = {r["item_name"]: r["value"] for r in forecast_rows}

    return result


def get_cached_periods(
    conn: sqlite3.Connection,
    ticker: str,
    statement: str,
) -> set[str]:
    """Return set of periods already cached for a given ticker+statement."""
    rows = conn.execute(
        """
        SELECT DISTINCT period FROM financial_items
        WHERE ticker = ? AND statement = ?
        """,
        (ticker, statement),
    ).fetchall()
    return {r["period"] for r in rows}


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------

def upsert_price(
    conn: sqlite3.Connection,
    ticker: str,
    date: str,
    close: float | None,
    volume: int | None,
    *,
    shares_outstanding: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO prices (ticker, date, close, volume, shares_outstanding, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            close=excluded.close,
            volume=excluded.volume,
            shares_outstanding=excluded.shares_outstanding,
            updated_at=excluded.updated_at
        """,
        (ticker, date, close, volume, shares_outstanding, _now()),
    )


def get_latest_price(
    conn: sqlite3.Connection,
    ticker: str,
) -> float | None:
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return row["close"] if row else None


def get_latest_price_with_shares(
    conn: sqlite3.Connection,
    ticker: str,
) -> dict[str, float | int | str | None]:
    """Return latest cached price, shares_outstanding, and updated_at."""
    row = conn.execute(
        """
        SELECT close, shares_outstanding, updated_at
        FROM prices WHERE ticker = ?
        ORDER BY date DESC LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if row is None:
        return {"price": None, "shares_outstanding": None, "updated_at": None}
    return {
        "price": row["close"],
        "shares_outstanding": row["shares_outstanding"],
        "updated_at": row["updated_at"],
    }
