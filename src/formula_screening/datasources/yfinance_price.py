"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from formula_screening.db.repository import (
    get_latest_price_with_shares,
    upsert_price,
)

logger = logging.getLogger("formula_screening.yfinance_price")
print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output

_BATCH_SIZE = 100
_SHARES_WORKERS = 10


def fetch_current(ticker: str, *, proxy: str | None = None) -> dict[str, float | int | None]:
    """Fetch the latest price and shares outstanding for a Japanese stock.

    Args:
        ticker: Bare ticker code (e.g. "7203"). The ".T" suffix is appended
                automatically for Tokyo Stock Exchange.
        proxy: Optional HTTP proxy URL (e.g. ``http://host:port``).

    Returns:
        {"price": float | None, "shares_outstanding": int | None}
    """
    symbol = f"{ticker}.T"
    logger.debug("Fetching price for %s", symbol)

    try:
        t = yf.Ticker(symbol, proxy=proxy)
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


def _download_prices_batch(
    symbols: list[str],
    proxy: str | None = None,
) -> dict[str, float | None]:
    """Batch-download close prices via yf.download.

    Raises:
        SystemExit: If rate-limited (< 30% success).
    """
    if not symbols:
        return {}
    kwargs: dict = {"period": "1d", "progress": False}
    if proxy:
        kwargs["proxy"] = proxy
    data = yf.download(symbols, **kwargs)
    if data.empty:
        return {}
    close = data["Close"]
    if isinstance(close, pd.Series):
        val = close.iloc[-1]
        return {symbols[0]: float(val) if pd.notna(val) else None}
    row = close.iloc[-1]
    valid = row.dropna()
    if len(valid) < len(symbols) * 0.3:
        print(f"ABORT: rate-limited — only {len(valid)}/{len(symbols)} prices returned", file=sys.stderr)
        sys.exit(1)
    return {
        sym: float(row[sym]) if pd.notna(row[sym]) else None
        for sym in row.index
    }


def _fetch_shares_one(args: tuple[str, str | None]) -> tuple[str, int | None]:
    """Fetch shares_outstanding for a single symbol. Thread-safe."""
    sym, proxy = args
    try:
        fi = yf.Ticker(sym, proxy=proxy).fast_info
        shares = fi.get("shares")
        return (sym, int(shares) if shares is not None else None)
    except Exception:
        return (sym, None)


def _fetch_shares_batch(
    symbols: list[str],
    proxy: str | None = None,
) -> dict[str, int | None]:
    """Fetch shares_outstanding in parallel via ThreadPoolExecutor."""
    result: dict[str, int | None] = {}
    work = [(sym, proxy) for sym in symbols]
    with concurrent.futures.ThreadPoolExecutor(max_workers=_SHARES_WORKERS) as pool:
        for sym, shares in pool.map(_fetch_shares_one, work):
            result[sym] = shares
    return result


def fetch_and_cache_prices(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    force: bool = False,
    proxy: str | None = None,
) -> dict[str, int]:
    """Fetch prices from yfinance and cache in DB.

    Uses ``yf.download`` for batch price retrieval and parallel
    ``fast_info`` calls for shares outstanding.  Exits on rate-limit.

    Args:
        conn: Database connection.
        tickers: List of bare ticker codes.
        force: If True, re-fetch even if cached < 1 day.
        proxy: Optional HTTP proxy URL.

    Returns:
        {"fetched": N, "skipped": N, "failed": N}
    """
    if force:
        targets = list(tickers)
    else:
        targets = []
        for ticker in tickers:
            cached = get_latest_price_with_shares(conn, ticker)
            if is_price_stale(cached["updated_at"]):
                targets.append(ticker)

    skipped = len(tickers) - len(targets)
    fetched = 0
    failed = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Targets: {len(targets)} tickers ({skipped} skipped)")
    if proxy:
        print(f"Proxy: {proxy}")

    for batch_start in range(0, len(targets), _BATCH_SIZE):
        batch = targets[batch_start : batch_start + _BATCH_SIZE]
        symbols = [f"{t}.T" for t in batch]

        print(f"[{batch_start + 1}-{batch_start + len(batch)}/{len(targets)}] prices...")
        prices = _download_prices_batch(symbols, proxy=proxy)

        print(f"[{batch_start + 1}-{batch_start + len(batch)}/{len(targets)}] shares ({_SHARES_WORKERS} workers)...")
        shares = _fetch_shares_batch(symbols, proxy=proxy)

        for ticker, sym in zip(batch, symbols):
            price = prices.get(sym)
            share_count = shares.get(sym)
            if price is None and share_count is None:
                failed += 1
                continue
            try:
                upsert_price(
                    conn, ticker, today,
                    close=price,
                    volume=None,
                    shares_outstanding=share_count,
                )
                fetched += 1
            except Exception:
                failed += 1

        conn.commit()
        print(f"  => fetched={fetched}, failed={failed}")

    return {"fetched": fetched, "skipped": skipped, "failed": failed}
