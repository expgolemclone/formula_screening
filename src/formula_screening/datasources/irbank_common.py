"""Shared browser-based fetch and parallel worker for IR BANK scrapers.

Both ``irbank_bs`` and ``irbank_forecast`` need the same retry/proxy/stats
machinery.  This module provides that common skeleton so each scraper only
has to supply a validation function and a row-building callback.

Page fetching is delegated to the Node.js browser service
(``formula_screening.browser.BrowserService``).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

logger: logging.Logger = logging.getLogger("formula_screening.irbank_common")

_IRBANK_URL_TEMPLATE: str = "https://irbank.net/{ticker}/{path}"
_MAX_RETRIES: int = MAGIC["scrape"]["max_retries"]
_PROXY_REMOVE_ON_ERROR: bool = MAGIC["scrape"]["proxy_remove_on_error"]


def fetch_irbank_html(
    ticker: str,
    path: str,
    pool: ProxyPool,
    *,
    validate_fn: Callable[[str], bool],
    browser: BrowserService,
    timeout: int = MAGIC["browser"]["page_timeout"],
) -> str | None:
    """Fetch an IR BANK page via the browser service and return HTML if *validate_fn* passes.

    Args:
        ticker: Stock ticker code.
        path: URL path segment (e.g. ``"bs"``, ``"results"``).
        pool: A ``ProxyPool`` instance.
        validate_fn: Callable that returns True when the HTML is usable.
        browser: A running ``BrowserService`` instance.
        timeout: Page navigation timeout in milliseconds.

    Returns:
        HTML string if successful, None on failure.
    """
    from formula_screening.browser import BrowserResponse, BrowserServiceError
    from formula_screening.stealth import ProxyUnavailableError, random_delay

    def _handle_proxy_error() -> None:
        if _PROXY_REMOVE_ON_ERROR:
            pool.report_failure()
        else:
            pool.rotate()

    url: str = _IRBANK_URL_TEMPLATE.format(ticker=ticker, path=path)

    for attempt in range(_MAX_RETRIES):
        proxy_url: str | None = pool.get()
        if proxy_url is None:
            raise ProxyUnavailableError("Proxy pool exhausted during request execution")

        try:
            resp: BrowserResponse = browser.fetch(url, proxy=proxy_url, timeout=timeout)
        except BrowserServiceError as exc:
            logger.warning(
                "Browser service error for %s (attempt %d): %s",
                ticker, attempt + 1, exc,
            )
            _handle_proxy_error()
            continue

        if resp.status == 200 and resp.html is not None and validate_fn(resp.html):
            return resp.html

        if resp.error is not None:
            logger.warning(
                "Fetch error for %s (attempt %d): %s",
                ticker, attempt + 1, resp.error,
            )
            _handle_proxy_error()
            continue

        if resp.html is not None and not validate_fn(resp.html):
            snippet: str = resp.html[:500].replace("\n", " ")
            logger.warning(
                "Blocked for %s (status=%d, attempt %d): %s",
                ticker, resp.status, attempt + 1, snippet,
            )
            _handle_proxy_error()
            random_delay(
                MAGIC["scrape"]["rate_limit_delay_min"],
                MAGIC["scrape"]["rate_limit_delay_max"],
            )
            continue

        logger.warning(
            "Unexpected status %d for %s (attempt %d)",
            resp.status, ticker, attempt + 1,
        )
        return None

    return None


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

    Args:
        source: Value for the ``source`` column and skip-check filter.
        process_fn: ``(ticker, html) -> list[dict]`` returning DB rows.
        on_html_fn: Optional callback ``(ticker, html, conn)`` invoked
            after a successful fetch (e.g. to extract company name).
        fetch_path: URL path passed to :func:`fetch_irbank_html`.
        validate_fn: HTML validation function for the fetch.
        browser: A running ``BrowserService`` instance.
    """
    from formula_screening.db.repository import upsert_financial_items_bulk
    from formula_screening.db.schema import get_connection
    from formula_screening.stealth import random_delay

    conn: sqlite3.Connection = get_connection()
    try:
        for ticker in tickers:
            with stats_lock:
                counter[0] += 1
                seq: int = counter[0]

            if not force:
                existing = conn.execute(
                    "SELECT 1 FROM financial_items WHERE ticker = ? AND source = ? LIMIT 1",
                    (ticker, source),
                ).fetchone()
                if existing:
                    with stats_lock:
                        stats["skip"] += 1
                    continue

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
