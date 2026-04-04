"""Detect scraper/parser source changes and invalidate stale DB cache.

Computes SHA256 of each datasource module, compares against saved hashes,
deletes corresponding cached rows when a hash differs, and optionally
re-fetches the invalidated data sources.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from formula_screening.config import DB_PATH, HASH_FILE

if TYPE_CHECKING:
    from collections.abc import Callable

    from formula_screening.stealth import ProxyPool

logger = logging.getLogger("formula_screening.cache_invalidation")

_DATASOURCES_DIR = Path(__file__).resolve().parent / "datasources"

# ---------------------------------------------------------------------------
# File → DB source mapping
# ---------------------------------------------------------------------------

_FILE_SOURCE_MAP: dict[str, list[str]] = {
    "irbank.py": ["irbank"],
    "irbank_bs.py": ["irbank_bs"],
    "irbank_forecast.py": ["irbank_forecast"],
    "irbank_common.py": ["irbank_bs", "irbank_forecast"],
}

_PRICE_FILES: set[str] = {"yfinance_price.py"}

_TRACKED_FILES: set[str] = {*_FILE_SOURCE_MAP, *_PRICE_FILES}

# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def compute_hashes(datasources_dir: Path | None = None) -> dict[str, str]:
    """Return ``{filename: sha256hex}`` for every tracked datasource file."""
    base = datasources_dir or _DATASOURCES_DIR
    hashes: dict[str, str] = {}
    for name in sorted(_TRACKED_FILES):
        path = base / name
        if path.is_file():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def load_saved_hashes(path: Path | None = None) -> dict[str, str]:
    """Load previously saved hashes, or empty dict if the file is missing."""
    p = path or HASH_FILE
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_hashes(hashes: dict[str, str], path: Path | None = None) -> None:
    """Persist current hashes."""
    p = path or HASH_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")


def detect_changes(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Return filenames whose hash differs or is newly tracked."""
    return [
        name for name in sorted(_TRACKED_FILES)
        if name in new and old.get(name) != new[name]
    ]


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def invalidate_cache(
    changed_files: list[str],
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Delete cached rows corresponding to *changed_files*.

    Returns ``{description: deleted_count}`` summary.
    """
    sources: set[str] = set()
    delete_prices = False

    for name in changed_files:
        if name in _PRICE_FILES:
            delete_prices = True
        sources.update(_FILE_SOURCE_MAP.get(name, []))

    if not sources and not delete_prices:
        return {}

    result: dict[str, int] = {}
    own_conn = conn is None
    if own_conn:
        from formula_screening.db.schema import get_connection
        conn = get_connection()
    try:
        for source in sorted(sources):
            cur = conn.execute(
                "DELETE FROM financial_items WHERE source = ?", (source,),
            )
            result[f"financial_items[source={source}]"] = cur.rowcount

        if delete_prices:
            cur = conn.execute("DELETE FROM prices")
            result["prices"] = cur.rowcount

        conn.commit()
    finally:
        if own_conn:
            conn.close()

    return result


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def check_and_invalidate(*, verbose: bool = False) -> list[str]:
    """Compare hashes, invalidate changed caches, save new hashes.

    Returns list of changed filenames (empty if nothing changed).
    """
    current = compute_hashes()
    saved = load_saved_hashes()
    changed = detect_changes(saved, current)

    if not changed:
        if verbose:
            logger.info("Scraper hashes unchanged — cache is up to date.")
        return []

    print("Scraper source changed:")
    for name in changed:
        print(f"  - {name}")

    if DB_PATH.is_file():
        deleted = invalidate_cache(changed)
        if deleted:
            print("Invalidated cache:")
            for desc, count in deleted.items():
                print(f"  {desc}: {count} rows")
    else:
        print("DB not found; skipping invalidation.")

    save_hashes(current)
    return changed


def refresh_stale_sources(
    changed_files: list[str],
    *,
    proxy_pool: ProxyPool,
) -> None:
    """Re-fetch data sources corresponding to *changed_files*.

    Runs the appropriate import/scrape/fetch for each changed datasource
    module, reusing the existing CLI logic.
    """
    from formula_screening.db.repository import get_all_tickers
    from formula_screening.db.schema import get_connection

    sources: set[str] = set()
    refetch_prices = False
    for name in changed_files:
        if name in _PRICE_FILES:
            refetch_prices = True
        sources.update(_FILE_SOURCE_MAP.get(name, []))

    conn = get_connection()
    try:
        tickers = get_all_tickers(conn)

        if "irbank" in sources:
            _import_irbank(conn)

        if "irbank_bs" in sources:
            _scrape_bs(tickers, proxy_pool)

        if "irbank_forecast" in sources:
            _scrape_forecast(tickers, proxy_pool)

        if refetch_prices and tickers:
            _fetch_prices(conn, tickers, proxy_pool)
    finally:
        conn.close()


def _import_irbank(conn: sqlite3.Connection) -> None:
    from formula_screening.config import IRBANK_DIR
    from formula_screening.datasources.irbank import import_irbank_json

    irbank_dir = IRBANK_DIR
    if irbank_dir.is_dir():
        print("\n[auto] import-irbank ...")
        total: int = import_irbank_json(conn, irbank_dir)
        print(f"  {total} items imported.")
    else:
        print(f"\n[auto] irbank data dir not found: {irbank_dir}")


def _scrape_bs(tickers: list[str], proxy_pool: ProxyPool) -> None:
    from formula_screening.cli import dispatch_scrape_workers
    from formula_screening.datasources.irbank_bs import scrape_bs_worker

    print("\n[auto] scrape-bs ...")
    dispatch_scrape_workers(
        tickers, proxy_pool,
        worker_fn=scrape_bs_worker,
        label="BS",
        force=True,
        extra_kwargs={"years": 1},
    )


def _scrape_forecast(tickers: list[str], proxy_pool: ProxyPool) -> None:
    from formula_screening.cli import dispatch_scrape_workers
    from formula_screening.datasources.irbank_forecast import (
        scrape_forecast_worker,
    )

    print("\n[auto] scrape-forecast ...")
    dispatch_scrape_workers(
        tickers, proxy_pool,
        worker_fn=scrape_forecast_worker,
        label="forecast",
        force=True,
    )


def _fetch_prices(conn: sqlite3.Connection, tickers: list[str], proxy_pool: ProxyPool) -> None:
    from formula_screening.datasources.yfinance_price import (
        fetch_and_cache_prices,
    )

    print(f"\n[auto] fetch-prices ({len(tickers)} tickers) ...")
    result: dict[str, int] = fetch_and_cache_prices(conn, tickers, force=True, pool=proxy_pool)
    print(
        f"  fetched={result['fetched']}, "
        f"skipped={result['skipped']}, "
        f"failed={result['failed']}"
    )


def ensure_data_available(*, get_proxy_pool: Callable[[], ProxyPool]) -> None:
    """Check DB for missing data sources and auto-fetch if empty.

    The proxy pool is created lazily via *get_proxy_pool* so that the
    expensive proxy discovery is skipped when all data is already present.
    """
    from formula_screening.db.repository import get_all_tickers
    from formula_screening.db.schema import get_connection

    conn = get_connection()
    try:
        # Check which sources have data
        missing_sources: list[str] = []
        for source in ("irbank", "irbank_bs", "irbank_forecast"):
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM financial_items WHERE source = ?",
                (source,),
            ).fetchone()
            if row["cnt"] == 0:
                missing_sources.append(source)

        price_row = conn.execute("SELECT COUNT(*) AS cnt FROM prices").fetchone()
        missing_prices = price_row["cnt"] == 0

        if not missing_sources and not missing_prices:
            return

        print("Missing data detected, auto-fetching:")
        for s in missing_sources:
            print(f"  - {s}")
        if missing_prices:
            print("  - prices")

        proxy_pool = get_proxy_pool()

        if "irbank" in missing_sources:
            _import_irbank(conn)

        tickers = get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB after import. Cannot scrape.")
            return

        if "irbank_bs" in missing_sources:
            _scrape_bs(tickers, proxy_pool)

        if "irbank_forecast" in missing_sources:
            _scrape_forecast(tickers, proxy_pool)

        if missing_prices:
            _fetch_prices(conn, tickers, proxy_pool)

        save_hashes(compute_hashes())
        print("\nAuto-fetch complete.")
    finally:
        conn.close()


