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

from formula_screening.config import DATA_DIR, MAGIC

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
    from formula_screening.scrape.irbank_common import fetch_irbank_html
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


def _on_bs_html(ticker: str, html: str, conn: sqlite3.Connection) -> None:
    """Extract company name from BS page and upsert into stocks table."""
    from formula_screening.scrape.irbank_bs import parse_company_name
    from formula_screening.db.repository import upsert_stock

    name: str | None = parse_company_name(html)
    if name:
        upsert_stock(conn, ticker, name=name, sector="", market="")


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
    from formula_screening.scrape.irbank_bs import (
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
    from formula_screening.scrape.irbank_forecast import (
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
# Shares-outstanding worker (kabutan)
# ---------------------------------------------------------------------------


def scrape_shares_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    interval: float = MAGIC["scrape"]["interval"],
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Fetch ``発行済株式数`` from kabutan for a chunk of tickers.

    Mirrors the structure of :func:`scrape_worker` but writes to
    ``stocks.shares_outstanding`` via :func:`upsert_shares_outstanding`
    instead of the ``financial_items`` EAV table — shares are a per-ticker
    attribute, not a period-keyed statement item. kabutan serves usable HTML
    to plain HTTPS clients, so no BrowserService is involved.
    """
    from formula_screening.db.repository import upsert_shares_outstanding
    from formula_screening.db.schema import get_connection
    from formula_screening.scrape.kabutan_shares import (
        build_shares_row,
        fetch_kabutan_html,
    )
    from formula_screening.stealth import random_delay

    del force  # skip-filter already applied upstream in _run_scrape_workers

    conn: sqlite3.Connection = get_connection()
    try:
        for ticker in tickers:
            with stats_lock:
                counter[0] += 1
                seq: int = counter[0]

            html: str | None = fetch_kabutan_html(ticker, pool)
            if html is None:
                with stats_lock:
                    stats["fail"] += 1
                    print(f"[{seq}/{total}] {ticker} FAILED", flush=True)
            else:
                row = build_shares_row(ticker, html)
                if row is None:
                    with stats_lock:
                        stats["fail"] += 1
                        print(f"[{seq}/{total}] {ticker} NO DATA", flush=True)
                else:
                    upsert_shares_outstanding(conn, ticker, row["shares_outstanding"])
                    conn.commit()
                    with stats_lock:
                        stats["ok"] += 1
                        print(
                            f"[{seq}/{total}] {ticker} OK ({row['shares_outstanding']:,})",
                            flush=True,
                        )

            random_delay(interval, interval + MAGIC["scrape"]["interval_jitter"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Price worker
# ---------------------------------------------------------------------------


def fetch_prices_stooq(
    tickers: list[str],
    *,
    get_browser: Callable[[], BrowserService] | None = None,
    force: bool,
) -> dict[str, int]:
    """Fetch prices for all tickers at once via Stooq daily text file.

    Uses an existing local ``stooq/YYYYMMDD_d.txt`` if available; only when no
    local file is present does it call ``get_browser()`` to start a browser and
    download. This keeps browser startup lazy so callers that already have a
    fresh local dump pay nothing for the orchestration.

    Returns stats dict with ``ok``, ``skip``, and ``fail`` counts.
    """
    from formula_screening.scrape.stooq_price import (
        download_daily_txt,
        find_latest_daily_txt,
        parse_daily_txt,
    )
    from formula_screening.db.repository import (
        get_fresh_price_tickers,
        upsert_price,
    )
    from formula_screening.db.schema import get_connection

    stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0}
    conn: sqlite3.Connection = get_connection()

    try:
        if force:
            target_tickers: set[str] = set(tickers)
        else:
            fresh: set[str] = get_fresh_price_tickers(conn, MAGIC["price"]["stale_days"])
            target_tickers = set(tickers) - fresh
            stats["skip"] = len(set(tickers) & fresh)

        if not target_tickers:
            print(f"All {len(tickers)} tickers already fresh, nothing to fetch.", flush=True)
            return stats

        stooq_dir = DATA_DIR / "stooq"
        stooq_dir.mkdir(parents=True, exist_ok=True)
        txt_path = find_latest_daily_txt(stooq_dir, max_age_days=MAGIC["price"]["stale_days"])

        if txt_path is None:
            if get_browser is None:
                print("No local Stooq file found and no browser available.", flush=True)
                return stats
            print(f"Downloading Stooq daily txt ({len(target_tickers)} tickers needed)...", flush=True)
            txt_path = download_daily_txt(get_browser(), str(stooq_dir))

        print(f"Parsing {txt_path.name}...", flush=True)
        prices = parse_daily_txt(txt_path, tickers=target_tickers)

        for ticker in target_tickers:
            if ticker in prices:
                row = prices[ticker]
                upsert_price(
                    conn, ticker, row["date"],
                    close=row["price"],
                    volume=None,
                )
                stats["ok"] += 1
            else:
                stats["fail"] += 1

        conn.commit()
        print(
            f"Stooq fetch complete: {stats['ok']} ok, {stats['skip']} skipped, {stats['fail']} not found.",
            flush=True,
        )
    finally:
        conn.close()

    return stats

