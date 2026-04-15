"""Auto-bootstrap empty DB by importing IR BANK JSON and scraping missing sources."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

logger = logging.getLogger("formula_screening.bootstrap")

DATA_SOURCES: frozenset[str] = frozenset(
    {"irbank", "irbank_bs", "irbank_forecast", "prices"}
)
_FINANCIAL_ITEM_SOURCES: tuple[str, ...] = ("irbank", "irbank_bs", "irbank_forecast")
_SCRAPE_SOURCES: frozenset[str] = frozenset({"irbank_bs", "irbank_forecast"})


def ensure_data_available(
    *,
    required_sources: Iterable[str],
    get_proxy_pool: Callable[[], ProxyPool],
    get_browser: Callable[[], BrowserService],
) -> None:
    """Check DB for missing data sources and auto-fetch only what's needed.

    ``get_proxy_pool`` and ``get_browser`` are invoked lazily. Neither is
    called when all required data is already present; furthermore, proxy
    acquisition only happens when live scraping (``irbank_bs`` /
    ``irbank_forecast``) is actually required, and browser startup is skipped
    for price imports if a local Stooq daily file is already on disk.
    """
    from formula_screening.db.repository import get_all_tickers
    from formula_screening.db.schema import get_connection

    required: frozenset[str] = frozenset(required_sources)
    unknown: set[str] = set(required) - DATA_SOURCES
    if unknown:
        logger.warning("Unknown REQUIRED_SOURCES entries ignored: %s", sorted(unknown))
        required = required & DATA_SOURCES

    conn = get_connection()
    try:
        missing_sources: list[str] = [
            source
            for source in _FINANCIAL_ITEM_SOURCES
            if source in required and _is_source_empty(conn, source)
        ]

        missing_prices: bool = (
            "prices" in required and _is_prices_stale(conn)
        )

        if not missing_sources and not missing_prices:
            return

        print("Missing data detected, auto-fetching:")
        for source in missing_sources:
            print(f"  - {source}")
        if missing_prices:
            print("  - prices")

        if "irbank" in missing_sources:
            _import_irbank(conn)

        tickers: list[str] = get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB after import. Cannot scrape.")
            return

        needs_scrape: bool = bool(set(missing_sources) & _SCRAPE_SOURCES)
        proxy_pool: ProxyPool | None = get_proxy_pool() if needs_scrape else None
        browser: BrowserService | None = get_browser() if needs_scrape else None

        if "irbank_bs" in missing_sources and proxy_pool is not None and browser is not None:
            _scrape_bs(tickers, proxy_pool, browser=browser)

        if "irbank_forecast" in missing_sources and proxy_pool is not None and browser is not None:
            _scrape_forecast(tickers, proxy_pool, browser=browser)

        if missing_prices:
            _fetch_prices(tickers, get_browser=get_browser)

        print("\nAuto-fetch complete.")
    finally:
        conn.close()


def _is_source_empty(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM financial_items WHERE source = ?",
        (source,),
    ).fetchone()
    return bool(row["cnt"] == 0)


def _is_prices_stale(conn: sqlite3.Connection) -> bool:
    from stock_db.storage.prices import get_fresh_price_tickers
    from stock_db.storage.stocks import get_all_tickers

    tickers: set[str] = get_all_tickers(conn)
    if not tickers:
        return False
    fresh: set[str] = get_fresh_price_tickers(conn, MAGIC["price"]["stale_days"])
    return bool(set(tickers) - fresh)


def _import_irbank(conn: sqlite3.Connection) -> None:
    from formula_screening.config import IRBANK_DIR
    from formula_screening.scrape.irbank import import_irbank_json

    irbank_dir = IRBANK_DIR
    if irbank_dir.is_dir():
        print("\n[auto] import-irbank ...")
        total: int = import_irbank_json(conn, irbank_dir)
        print(f"  {total} items imported.")
    else:
        print(f"\n[auto] irbank data dir not found: {irbank_dir}")


def _scrape_bs(
    tickers: list[str],
    proxy_pool: ProxyPool,
    *,
    browser: BrowserService,
    workers: int | None = None,
) -> None:
    from formula_screening.cli import dispatch_workers
    from formula_screening.worker import scrape_bs_worker

    print("\n[auto] scrape-bs ...")
    dispatch_workers(
        tickers, proxy_pool,
        worker_fn=scrape_bs_worker,
        label="BS",
        workers=workers or MAGIC["scrape"]["workers"],
        force=True,
        extra_kwargs={"years": 1, "browser": browser},
    )


def _scrape_forecast(
    tickers: list[str],
    proxy_pool: ProxyPool,
    *,
    browser: BrowserService,
    workers: int | None = None,
) -> None:
    from formula_screening.cli import dispatch_workers
    from formula_screening.worker import scrape_forecast_worker

    print("\n[auto] scrape-forecast ...")
    dispatch_workers(
        tickers, proxy_pool,
        worker_fn=scrape_forecast_worker,
        label="forecast",
        workers=workers or MAGIC["scrape"]["workers"],
        force=True,
        extra_kwargs={"browser": browser},
    )


def _fetch_prices(
    tickers: list[str],
    *,
    get_browser: Callable[[], BrowserService],
) -> None:
    from formula_screening.worker import fetch_prices_stooq

    print(f"\n[auto] fetch-prices via Stooq ({len(tickers)} tickers) ...")
    fetch_prices_stooq(tickers, get_browser=get_browser, force=True)
