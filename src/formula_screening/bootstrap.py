"""Auto-bootstrap empty DB by importing IR BANK JSON and scraping missing sources."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from collections.abc import Callable

    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

logger = logging.getLogger("formula_screening.bootstrap")


def ensure_data_available(
    *,
    get_proxy_pool: Callable[[], ProxyPool],
    get_browser: Callable[[], BrowserService],
) -> None:
    """Check DB for missing data sources and auto-fetch if empty.

    The proxy pool and browser service are created lazily so that
    the expensive startup is skipped when all data is already present.
    """
    from formula_screening.db.repository import get_all_tickers
    from formula_screening.db.schema import get_connection

    conn = get_connection()
    try:
        missing_sources: list[str] = []
        for source in ("irbank", "irbank_bs", "irbank_forecast"):
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM financial_items WHERE source = ?",
                (source,),
            ).fetchone()
            if row["cnt"] == 0:
                missing_sources.append(source)

        price_row = conn.execute("SELECT COUNT(*) AS cnt FROM prices").fetchone()
        missing_prices: bool = price_row["cnt"] == 0

        if not missing_sources and not missing_prices:
            return

        print("Missing data detected, auto-fetching:")
        for s in missing_sources:
            print(f"  - {s}")
        if missing_prices:
            print("  - prices")

        proxy_pool: ProxyPool = get_proxy_pool()

        if "irbank" in missing_sources:
            _import_irbank(conn)

        tickers: list[str] = get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB after import. Cannot scrape.")
            return

        needs_browser: bool = (
            "irbank_bs" in missing_sources
            or "irbank_forecast" in missing_sources
            or missing_prices
        )
        browser: BrowserService | None = get_browser() if needs_browser else None

        if "irbank_bs" in missing_sources and browser is not None:
            _scrape_bs(tickers, proxy_pool, browser=browser)

        if "irbank_forecast" in missing_sources and browser is not None:
            _scrape_forecast(tickers, proxy_pool, browser=browser)

        if missing_prices and browser is not None:
            _fetch_prices(tickers, browser)

        print("\nAuto-fetch complete.")
    finally:
        conn.close()


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


def _fetch_prices(tickers: list[str], browser: BrowserService | None = None) -> None:
    from formula_screening.worker import fetch_prices_stooq

    print(f"\n[auto] fetch-prices via Stooq ({len(tickers)} tickers) ...")
    fetch_prices_stooq(tickers, browser, force=True)
