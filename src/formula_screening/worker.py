"""Worker orchestration for parallel scraping and price fetching.

Separated from datasource modules so that changes to worker logic
(progress display, skip checks, stats) do not trigger scraper-hash-based
cache invalidation.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool


# ---------------------------------------------------------------------------
# Generic IR BANK scrape worker
# ---------------------------------------------------------------------------


def scrape_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    source: str,
    process_fn: Callable[[str, str], list[dict[str, str | float]]],
    on_html_fn: Callable[[str, str, sqlite3.Connection], None] | None = None,
    fetch_path: str,
    validate_fn: Callable[[str], bool],
    browser: BrowserService,
    interval: float = MAGIC["scrape"]["interval"],
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Process a chunk of tickers, storing results in the DB.

    Designed to run inside a ``ThreadPoolExecutor``.
    """
    from formula_screening.datasources.irbank_common import fetch_irbank_html
    from formula_screening.db.repository import upsert_financial_items_bulk
    from formula_screening.db.schema import get_connection
    from formula_screening.stealth import random_delay

    conn: sqlite3.Connection = get_connection()
    try:
        for ticker in tickers:
            if not force:
                existing = conn.execute(
                    "SELECT 1 FROM financial_items WHERE ticker = ? AND source = ? LIMIT 1",
                    (ticker, source),
                ).fetchone()
                if existing:
                    with stats_lock:
                        stats["skip"] += 1
                    continue

            with stats_lock:
                counter[0] += 1
                seq: int = counter[0]

            html: str | None = fetch_irbank_html(
                ticker, fetch_path, pool,
                validate_fn=validate_fn, browser=browser,
            )
            if html is None:
                with stats_lock:
                    print(f"[{seq}/{total}] {ticker} FAILED", flush=True)
                    stats["fail"] += 1
                continue

            if on_html_fn is not None:
                on_html_fn(ticker, html, conn)

            rows: list[dict[str, str | float]] = process_fn(ticker, html)

            if rows:
                upsert_financial_items_bulk(conn, rows)
                conn.commit()
                with stats_lock:
                    stats["ok"] += 1
                    print(f"[{seq}/{total}] {ticker} OK ({len(rows)} items)", flush=True)
            else:
                with stats_lock:
                    stats["fail"] += 1
                    print(f"[{seq}/{total}] {ticker} NO DATA", flush=True)

            random_delay(interval, interval + MAGIC["scrape"]["interval_jitter"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# BS worker
# ---------------------------------------------------------------------------


def scrape_bs_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    years: int = 1,
    browser: BrowserService,
    interval: float = MAGIC["scrape"]["interval"],
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Scrape detailed BS data for a chunk of tickers."""
    from formula_screening.datasources.irbank_bs import (
        _on_bs_html,
        _validate_bs_html,
        build_bs_rows,
    )

    def _process(ticker: str, html: str) -> list[dict[str, str | float]]:
        return build_bs_rows(ticker, html, years=years)

    scrape_worker(
        tickers,
        pool,
        source="irbank_bs",
        process_fn=_process,
        on_html_fn=_on_bs_html,
        fetch_path="bs",
        validate_fn=_validate_bs_html,
        browser=browser,
        interval=interval,
        force=force,
        stats=stats,
        stats_lock=stats_lock,
        total=total,
        counter=counter,
    )


# ---------------------------------------------------------------------------
# Forecast worker
# ---------------------------------------------------------------------------


def scrape_forecast_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    browser: BrowserService,
    interval: float = MAGIC["scrape"]["interval"],
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Scrape forecast data for a chunk of tickers."""
    from formula_screening.datasources.irbank_forecast import (
        build_forecast_rows,
        validate_results_html,
    )

    scrape_worker(
        tickers,
        pool,
        source="irbank_forecast",
        process_fn=build_forecast_rows,
        fetch_path="results",
        validate_fn=validate_results_html,
        browser=browser,
        interval=interval,
        force=force,
        stats=stats,
        stats_lock=stats_lock,
        total=total,
        counter=counter,
    )


# ---------------------------------------------------------------------------
# Price worker
# ---------------------------------------------------------------------------


def fetch_prices_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    interval: float,
    force: bool,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Fetch price + shares for a chunk of tickers via yfinance."""
    from formula_screening.datasources.yfinance_price import (
        _fetch_one,
        is_price_stale,
    )
    from formula_screening.db.repository import (
        get_latest_price_with_shares,
        upsert_price,
    )
    from formula_screening.db.schema import get_connection
    from formula_screening.stealth import random_delay

    conn: sqlite3.Connection = get_connection()
    today: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        for ticker in tickers:
            if not force:
                cached = get_latest_price_with_shares(conn, ticker)
                if not is_price_stale(cached["updated_at"]):
                    with stats_lock:
                        stats["skip"] += 1
                    continue

            with stats_lock:
                counter[0] += 1
                seq: int = counter[0]

            result = _fetch_one(ticker, pool)
            price = result["price"]
            shares = result["shares_outstanding"]

            if price is None and shares is None:
                with stats_lock:
                    stats["fail"] += 1
                    print(f"[{seq}/{total}] {ticker} FAILED", flush=True)
                random_delay(interval, interval + MAGIC["price"]["interval_jitter"])
                continue

            upsert_price(
                conn, ticker, today,
                close=price,
                volume=None,
                shares_outstanding=shares,
            )
            conn.commit()

            with stats_lock:
                stats["ok"] += 1
                print(f"[{seq}/{total}] {ticker} OK", flush=True)

            random_delay(interval, interval + MAGIC["price"]["interval_jitter"])
    finally:
        conn.close()
