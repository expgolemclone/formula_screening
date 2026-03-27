"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import yfinance as yf

from formula_screening.db.repository import (
    get_latest_price_with_shares,
    upsert_price,
)

logger = logging.getLogger("formula_screening.yfinance_price")


def fetch_current(ticker: str) -> dict[str, float | int | None]:
    """Fetch the latest price and shares outstanding for a Japanese stock.

    Args:
        ticker: Bare ticker code (e.g. "7203"). The ".T" suffix is appended
                automatically for Tokyo Stock Exchange.

    Returns:
        {"price": float | None, "shares_outstanding": int | None}
    """
    symbol = f"{ticker}.T"
    logger.debug("Fetching price for %s", symbol)

    try:
        t = yf.Ticker(symbol)
        info = t.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = info.get("sharesOutstanding")

        if price is not None:
            price = float(price)
        if shares is not None:
            shares = int(shares)

        return {"price": price, "shares_outstanding": shares}
    except Exception:
        logger.warning("Failed to fetch price for %s", symbol, exc_info=True)
        return {"price": None, "shares_outstanding": None}


def is_price_stale(updated_at: str | None) -> bool:
    """Return True if the cached price is older than 1 day or missing."""
    if updated_at is None:
        return True
    try:
        ts = datetime.fromisoformat(updated_at)
        return datetime.now(timezone.utc) - ts > timedelta(days=1)
    except ValueError:
        return True


def fetch_and_cache_prices(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    force: bool = False,
    progress_interval: int = 100,
) -> dict[str, int]:
    """Fetch prices from yfinance and cache in DB.

    Args:
        conn: Database connection.
        tickers: List of ticker codes to fetch.
        force: If True, re-fetch even if cached < 1 day.
        progress_interval: Print progress every N tickers.

    Returns:
        {"fetched": N, "skipped": N, "failed": N}
    """
    total = len(tickers)
    fetched = 0
    skipped = 0
    failed = 0

    for i, ticker in enumerate(tickers, 1):
        if not force:
            cached = get_latest_price_with_shares(conn, ticker)
            if not is_price_stale(cached["updated_at"]):
                skipped += 1
                continue

        try:
            data = fetch_current(ticker)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            upsert_price(
                conn, ticker, today,
                close=data["price"],
                volume=None,
                shares_outstanding=data["shares_outstanding"],
            )
            conn.commit()
            fetched += 1
        except Exception:
            failed += 1

        if i % progress_interval == 0:
            logger.info(
                "Progress: %d/%d (fetched=%d, skipped=%d, failed=%d)",
                i, total, fetched, skipped, failed,
            )

    return {"fetched": fetched, "skipped": skipped, "failed": failed}
