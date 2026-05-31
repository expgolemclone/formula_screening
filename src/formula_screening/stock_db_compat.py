"""Compatibility layer for the current local ``stock_db`` package layout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from stock_db.paths import PROJECT_ROOT as STOCK_DB_PROJECT_ROOT
from stock_db.sources.price_refresh import (
    PriceRefreshCommandResult,
    PriceRefreshError,
    ensure_prices_fresh_for_api,
)
from stock_db.storage.connection import get_connection
from stock_db.storage.financials import get_financial_dict, get_historical_items, get_items_by_source
from stock_db.storage.prices import (
    get_latest_price_date,
    get_latest_price_with_shares,
    get_previous_jpx_business_day,
)
from stock_db.storage.schema import init_db
from stock_db.storage.stocks import (
    get_all_tickers as _get_all_tickers,
    get_stock_names as _get_stock_names,
    get_validation_targets as _get_validation_targets,
)


class ScreeningStock(TypedDict):
    ticker: str
    name: str
    price: float | None
    price_date: str | None
    shares_outstanding: int | None
    financials: dict[str, dict[str, float | None]]
    cf_history: list[tuple[str, dict[str, float | None]]]
    pl_history: list[tuple[str, dict[str, float | None]]]
    dividend_history: list[tuple[str, dict[str, float | None]]]


def _stock_db_path() -> Path:
    var_dir = Path(os.environ.get("STOCK_DB_VAR_DIR", str(STOCK_DB_PROJECT_ROOT / "var")))
    return var_dir / "db" / "stocks.db"


def ensure_prices_fresh() -> PriceRefreshCommandResult | None:
    return ensure_prices_fresh_for_api(db_path=_stock_db_path())


def get_all_tickers() -> list[str]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        return _get_all_tickers(conn)
    finally:
        conn.close()


def get_stock_names() -> dict[str, str]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        return _get_stock_names(conn)
    finally:
        conn.close()


def get_screening_tickers(limit: int | None = None) -> list[str]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        sql = """
            SELECT s.ticker
            FROM stocks s
            WHERE EXISTS (
                SELECT 1
                FROM financial_items fi
                WHERE fi.ticker = s.ticker
            )
            ORDER BY s.ticker
        """
        if limit is None:
            rows = conn.execute(sql).fetchall()
        else:
            rows = conn.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
        return [row["ticker"] for row in rows]
    finally:
        conn.close()


def load_screening_stocks(
    tickers: list[str] | None = None,
    *,
    fcf_periods: int = 10,
    pl_periods: int = 6,
    payout_periods: int = 10,
) -> list[ScreeningStock]:
    ensure_prices_fresh()
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        names = _get_stock_names(conn)
        selected_tickers = tickers if tickers is not None else _get_all_tickers(conn)
        result: list[ScreeningStock] = []
        for ticker in selected_tickers:
            price = get_latest_price_with_shares(conn, ticker)
            result.append(
                {
                    "ticker": ticker,
                    "name": names.get(ticker, ""),
                    "price": price["price"],
                    "price_date": price["price_date"],
                    "shares_outstanding": price["shares_outstanding"],
                    "financials": get_financial_dict(conn, ticker),
                    "cf_history": get_historical_items(conn, ticker, "cf", fcf_periods),
                    "pl_history": get_historical_items(conn, ticker, "pl", pl_periods),
                    "dividend_history": get_historical_items(conn, ticker, "dividend", payout_periods),
                }
            )
        return result
    finally:
        conn.close()


def get_stock_price_metadata() -> dict[str, str | None]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        latest_date = get_latest_price_date(conn)
    finally:
        conn.close()
    return {
        "price_date": latest_date.isoformat() if latest_date is not None else None,
        "target_price_date": get_previous_jpx_business_day().isoformat(),
    }


def get_validation_targets(limit: int) -> list[dict[str, object]]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        return [dict(row) for row in _get_validation_targets(conn, limit)]
    finally:
        conn.close()


def get_latest_balance_sheet(
    ticker: str,
) -> tuple[str | None, dict[str, float | None], str | None]:
    conn = get_connection(_stock_db_path())
    try:
        init_db(conn)
        rows = [
            row
            for source in ("xbrl_bs", "edinet_xbrl")
            for row in get_items_by_source(conn, ticker, source)
            if row["statement"] in {"bs", "_status"}
        ]
    finally:
        conn.close()

    if not rows:
        return None, {}, "scrape_missing"

    status_row = next((row for row in rows if row["statement"] == "_status"), None)
    if status_row is not None:
        return None, {}, f"scrape_{status_row['item_name']}"

    period = str(rows[0]["period"])
    bs = {
        str(row["item_name"]): row["value"]
        for row in rows
        if row["period"] == period and row["statement"] == "bs"
    }
    return period, bs, None
