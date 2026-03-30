"""Shared HTTP fetch and parallel worker for IR BANK scrapers.

Both ``irbank_bs`` and ``irbank_forecast`` need the same retry/proxy/stats
machinery.  This module provides that common skeleton so each scraper only
has to supply a validation function and a row-building callback.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from formula_screening.config import MAGIC

logger = logging.getLogger("formula_screening.irbank_common")

_IRBANK_URL_TEMPLATE = "https://irbank.net/{ticker}/{path}"
_MAX_RETRIES = MAGIC["scrape"]["max_retries"]


def fetch_irbank_html(
    ticker: str,
    path: str,
    pool: object,
    *,
    validate_fn: Callable[[str], bool],
    timeout: int = MAGIC["scrape"]["timeout"],
) -> str | None:
    """Fetch an IR BANK page and return HTML if *validate_fn* passes.

    Args:
        ticker: Stock ticker code.
        path: URL path segment (e.g. ``"bs"``, ``"results"``).
        pool: A ``ProxyPool`` instance.
        validate_fn: Callable that returns True when the HTML is usable.
        timeout: HTTP request timeout in seconds.

    Returns:
        HTML string if successful, None on failure.
    """
    import requests

    from formula_screening.stealth import random_delay, random_ua

    url = _IRBANK_URL_TEMPLATE.format(ticker=ticker, path=path)

    for attempt in range(_MAX_RETRIES):
        proxy_url = pool.get()
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": random_ua()},
                proxies=proxies,
                timeout=timeout,
            )
            if resp.status_code == 200 and validate_fn(resp.text):
                return resp.text
            if resp.status_code == 429 or "html" in resp.headers.get("Content-Type", ""):
                logger.info("Rate-limited for %s (attempt %d), rotating...", ticker, attempt + 1)
                pool.report_failure()
                random_delay(
                    MAGIC["scrape"]["rate_limit_delay_min"],
                    MAGIC["scrape"]["rate_limit_delay_max"],
                )
                continue
            return None
        except requests.RequestException:
            pool.report_failure()
            continue
    return None


def scrape_worker(
    tickers: list[str],
    pool: object,
    *,
    source: str,
    process_fn: Callable[[str, str], list[dict]],
    on_html_fn: Callable[[str, str, object], None] | None = None,
    fetch_path: str,
    validate_fn: Callable[[str], bool],
    years: int = 1,
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
    """
    from formula_screening.db.repository import upsert_financial_items_bulk
    from formula_screening.db.schema import get_connection
    from formula_screening.stealth import random_delay

    conn = get_connection()
    try:
        for ticker in tickers:
            with stats_lock:
                counter[0] += 1
                seq = counter[0]

            if not force:
                existing = conn.execute(
                    "SELECT 1 FROM financial_items WHERE ticker = ? AND source = ? LIMIT 1",
                    (ticker, source),
                ).fetchone()
                if existing:
                    with stats_lock:
                        stats["skip"] += 1
                    continue

            html = fetch_irbank_html(ticker, fetch_path, pool, validate_fn=validate_fn)
            if html is None:
                with stats_lock:
                    print(f"[{seq}/{total}] {ticker} FAILED", flush=True)
                    stats["fail"] += 1
                continue

            if on_html_fn is not None:
                on_html_fn(ticker, html, conn)

            rows = process_fn(ticker, html)

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
