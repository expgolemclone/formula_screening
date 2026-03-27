"""Fetch financial data via yfinance and store in DB."""

from __future__ import annotations

import logging
import sqlite3
import time

import yfinance as yf

from formula_screening.db.repository import (
    get_all_tickers,
    upsert_financial_items_bulk,
)

logger = logging.getLogger("formula_screening.edinetdb")

_PL_FIELDS: dict[str, str] = {
    "Total Revenue": "revenue",
    "Cost Of Revenue": "cost_of_revenue",
    "Operating Income": "operating_income",
    "Pretax Income": "ordinary_income",
    "Net Income": "net_income",
    "Basic EPS": "basic_eps",
}

_BS_FIELDS: dict[str, str] = {
    "Total Assets": "total_assets",
    "Stockholders Equity": "stockholders_equity",
    "Total Equity Gross Minority Interest": "total_equity",
    "Total Debt": "total_debt",
}

_CF_FIELDS: dict[str, str] = {
    "Operating Cash Flow": "operating_cf",
    "Free Cash Flow": "free_cf",
}


def _extract_items(
    df: object,
    statement: str,
    ticker: str,
    field_map: dict[str, str],
) -> list[dict]:
    """Extract financial items from a yfinance DataFrame."""
    if df is None or df.empty:  # type: ignore[union-attr]
        return []

    rows: list[dict] = []
    for col in df.columns:  # type: ignore[union-attr]
        period = col.strftime("%Y-%m") if hasattr(col, "strftime") else str(col)
        for yf_name, item_name in field_map.items():
            if yf_name in df.index:  # type: ignore[union-attr]
                val = df.loc[yf_name, col]  # type: ignore[union-attr]
                if val is not None and not (isinstance(val, float) and val != val):
                    rows.append({
                        "ticker": ticker,
                        "period": period,
                        "statement": statement,
                        "item_name": item_name,
                        "value": float(val),
                        "source": "yfinance",
                    })
    return rows


def _extract_dividend(ticker_obj: yf.Ticker, ticker: str) -> list[dict]:
    """Extract annual DPS from yfinance dividend history."""
    divs = ticker_obj.dividends
    if divs is None or divs.empty:
        return []

    # Group by fiscal year (April–March for Japanese companies)
    annual: dict[str, float] = {}
    for date, amount in divs.items():
        dt = date.to_pydatetime()  # type: ignore[union-attr]
        # Japanese fiscal year ending March: dividends in Apr-Mar map to that ending March
        fy_year = dt.year if dt.month <= 3 else dt.year + 1
        period = f"{fy_year}-03"
        annual[period] = annual.get(period, 0.0) + float(amount)

    return [
        {
            "ticker": ticker,
            "period": period,
            "statement": "dividend",
            "item_name": "dps",
            "value": dps,
            "source": "yfinance",
        }
        for period, dps in annual.items()
    ]


def _fetch_ticker(ticker: str) -> list[dict]:
    """Fetch all financial data for a single ticker from yfinance."""
    symbol = f"{ticker}.T"
    logger.debug("Fetching financials for %s", symbol)

    try:
        t = yf.Ticker(symbol)
        items: list[dict] = []
        items.extend(_extract_items(t.financials, "pl", ticker, _PL_FIELDS))
        items.extend(_extract_items(t.balance_sheet, "bs", ticker, _BS_FIELDS))
        items.extend(_extract_items(t.cashflow, "cf", ticker, _CF_FIELDS))
        items.extend(_extract_dividend(t, ticker))
        return items
    except Exception:
        logger.warning("Failed to fetch financials for %s", symbol, exc_info=True)
        return []


def fetch_all_financials(
    conn: sqlite3.Connection,
    tickers: set[str] | None = None,
    years: int = 6,
) -> int:
    """Fetch financial data for all (or specified) tickers.

    Args:
        conn: Database connection.
        tickers: Set of tickers to fetch. If None, fetches all in DB.
        years: Number of fiscal years (yfinance typically provides up to 5).

    Returns:
        Total number of financial items saved.
    """
    if tickers is None:
        tickers = set(get_all_tickers(conn))

    total = 0
    ticker_list = sorted(tickers)

    for i, ticker in enumerate(ticker_list, 1):
        if i % 10 == 0:
            logger.info("Progress: %d/%d", i, len(ticker_list))

        items = _fetch_ticker(ticker)
        if items:
            upsert_financial_items_bulk(conn, items)
            conn.commit()
            total += len(items)
            logger.debug("%s: %d items saved", ticker, len(items))

        if i < len(ticker_list):
            time.sleep(0.5)

    logger.info("Fetched %d financial items for %d tickers", total, len(ticker_list))
    return total
