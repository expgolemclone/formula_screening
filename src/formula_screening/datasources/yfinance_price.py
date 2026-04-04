"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import concurrent.futures
import logging
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from formula_screening.config import MAGIC
from formula_screening.db.repository import (
    get_latest_price_with_shares,
    upsert_price,
)
from formula_screening.stealth import ProxyPool, create_session, random_delay

logger = logging.getLogger("formula_screening.yfinance_price")

_BATCH_SIZE = MAGIC["price"]["batch_size"]
_SHARES_WORKERS = MAGIC["price"]["shares_workers"]


def fetch_current(
    ticker: str,
    *,
    pool: ProxyPool | None = None,
) -> dict[str, float | int | None]:
    """Fetch the latest price and shares outstanding for a Japanese stock.

    Args:
        ticker: Bare ticker code (e.g. "7203"). The ".T" suffix is appended
                automatically for Tokyo Stock Exchange.
        pool: Optional ProxyPool for stealth session creation.

    Returns:
        {"price": float | None, "shares_outstanding": int | None}
    """
    symbol = f"{ticker}.T"
    logger.debug("Fetching price for %s", symbol)

    try:
        session = create_session(pool)
        t = yf.Ticker(symbol, session=session)
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
        return datetime.now(timezone.utc) - ts > timedelta(days=MAGIC["price"]["stale_days"])
    except ValueError:
        return True


class RateLimitError(Exception):
    """Raised when price fetching is rate-limited beyond retry capacity."""


def _download_prices_batch(
    symbols: list[str],
    session: object | None = None,
) -> dict[str, float | None]:
    """Batch-download close prices via yf.download.

    Raises:
        RateLimitError: If < 30% of symbols returned data.
    """
    if not symbols:
        return {}
    kwargs: dict = {"period": "1d", "progress": False}
    if session is not None:
        kwargs["session"] = session
    data = yf.download(symbols, **kwargs)
    if data.empty:
        raise RateLimitError("Empty response")
    close = data["Close"]
    if isinstance(close, pd.Series):
        val = close.iloc[-1]
        return {symbols[0]: float(val) if pd.notna(val) else None}
    row = close.iloc[-1]
    valid = row.dropna()
    if len(valid) < len(symbols) * MAGIC["price"]["rate_limit_threshold"]:
        raise RateLimitError(f"Only {len(valid)}/{len(symbols)} prices returned")
    return {
        sym: float(row[sym]) if pd.notna(row[sym]) else None
        for sym in row.index
    }


def _fetch_shares_one(args: tuple[str, object | None]) -> tuple[str, int | None]:
    """Fetch shares_outstanding for a single symbol. Thread-safe."""
    sym, session = args
    try:
        fi = yf.Ticker(sym, session=session).fast_info
        shares = fi.get("shares")
        return (sym, int(shares) if shares is not None else None)
    except Exception:
        return (sym, None)


def _fetch_shares_batch(
    symbols: list[str],
    session: object | None = None,
    *,
    workers: int = _SHARES_WORKERS,
) -> dict[str, int | None]:
    """Fetch shares_outstanding in parallel via ThreadPoolExecutor."""
    result: dict[str, int | None] = {}
    work = [(sym, session) for sym in symbols]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for sym, shares in executor.map(_fetch_shares_one, work):
            result[sym] = shares
    return result


_MAX_BATCH_RETRIES = MAGIC["price"]["max_retries"]


def _process_batch(
    conn: sqlite3.Connection,
    batch: list[str],
    symbols: list[str],
    pool: ProxyPool,
    today: str,
    *,
    workers: int = _SHARES_WORKERS,
) -> tuple[int, int]:
    """Download prices + shares for one batch, with proxy retry.

    Returns:
        (fetched_count, failed_count)
    """
    session = create_session(pool)
    for attempt in range(_MAX_BATCH_RETRIES):
        try:
            prices = _download_prices_batch(symbols, session=session)
            break
        except RateLimitError as e:
            print(f"  Rate-limited ({e}), rotating proxy... (attempt {attempt + 1}/{_MAX_BATCH_RETRIES})", flush=True)
            pool.report_failure()
            session = create_session(pool)
            if pool.exhausted:
                print("  All proxies exhausted, falling back to direct", file=sys.stderr, flush=True)
    else:
        raise RateLimitError("Rate-limited after all retries")

    shares = _fetch_shares_batch(symbols, session=session, workers=workers)

    fetched = 0
    failed = 0
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
    return fetched, failed


def fetch_and_cache_prices(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    force: bool = False,
    pool: ProxyPool | None = None,
    workers: int = _SHARES_WORKERS,
) -> dict[str, int]:
    """Fetch prices from yfinance and cache in DB.

    Uses ``yf.download`` for batch price retrieval and parallel
    ``fast_info`` calls for shares outstanding.  Rotates proxies
    on rate-limit, exits if all retries fail.

    Args:
        conn: Database connection.
        tickers: List of bare ticker codes.
        force: If True, re-fetch even if cached < 1 day.
        pool: ProxyPool instance. If None, auto-fetches proxies.
        workers: Number of parallel threads for shares fetch.

    Returns:
        {"fetched": N, "skipped": N, "failed": N}
    """
    if pool is None:
        pool = ProxyPool.from_auto()

    if force:
        targets = list(tickers)
    else:
        targets = []
        for ticker in tickers:
            cached = get_latest_price_with_shares(conn, ticker)
            if is_price_stale(cached["updated_at"]):
                targets.append(ticker)

    random.shuffle(targets)
    skipped = len(tickers) - len(targets)
    total_fetched = 0
    total_failed = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Targets: {len(targets)} tickers ({skipped} skipped)", flush=True)
    print(f"Proxy: {pool.get() or 'direct'}", flush=True)

    for batch_start in range(0, len(targets), _BATCH_SIZE):
        if batch_start > 0:
            random_delay()

        batch = targets[batch_start : batch_start + _BATCH_SIZE]
        symbols = [f"{t}.T" for t in batch]

        label = f"[{batch_start + 1}-{batch_start + len(batch)}/{len(targets)}]"
        print(f"{label} prices + shares...", flush=True)

        fetched, failed = _process_batch(conn, batch, symbols, pool, today, workers=workers)
        total_fetched += fetched
        total_failed += failed

        print(f"  => fetched={total_fetched}, failed={total_failed}", flush=True)

    return {"fetched": total_fetched, "skipped": skipped, "failed": total_failed}
