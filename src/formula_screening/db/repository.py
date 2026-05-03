"""Data access layer – delegates to stock_db.storage APIs."""

from __future__ import annotations

import sqlite3

from stock_db.storage.financials import get_financial_dict as _stock_db_get_financial_dict
from stock_db.storage.financials import get_historical_items
from stock_db.storage.prices import get_latest_price_with_shares
from stock_db.storage.stocks import get_all_tickers, get_stock_names

__all__ = [
    "get_all_tickers",
    "get_financial_dict",
    "get_historical_items",
    "get_latest_price_with_shares",
    "get_stock_names",
]


def get_financial_dict(
    conn: sqlite3.Connection,
    ticker: str,
    period: str | None = None,
) -> dict[str, dict[str, float | None]]:
    """Load financial data as nested dict, pre-populating statement keys.

    Wraps ``stock_db.storage.financials.get_financial_dict`` to guarantee
    that the result always contains ``pl``, ``bs``, ``cf``, ``dividend``,
    ``ss``, and ``forecast`` keys (as empty dicts when missing).
    """
    result = _stock_db_get_financial_dict(conn, ticker, period)
    for key in ("pl", "bs", "cf", "dividend", "ss", "forecast"):
        result.setdefault(key, {})
    return result
