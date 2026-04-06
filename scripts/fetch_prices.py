#!/usr/bin/env python3
"""Fetch and cache stock prices from yfinance into the screening DB.

Usage:
    uv run python scripts/fetch_prices.py [--ticker TICKER ...] [--force]
        [--workers N] [--proxy URL]

Prices are cached in the ``prices`` table with a timestamp.
Tickers whose price was fetched less than 1 day ago are skipped
unless ``--force`` is specified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project package is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.cli import dispatch_scrape_workers
from formula_screening.config import MAGIC
from formula_screening.datasources.yfinance_price import fetch_prices_worker
from formula_screening.db.repository import get_all_tickers
from formula_screening.db.schema import get_connection, init_db
from formula_screening.stealth import ProxyPool, ProxyUnavailableError


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache stock prices")
    parser.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached <1 day")
    parser.add_argument("--workers", type=int, default=MAGIC["price"]["workers"], help="Number of parallel workers")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    try:
        tickers: list[str] = args.ticker if args.ticker else get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
            sys.exit(1)
    finally:
        conn.close()

    try:
        pool: ProxyPool = ProxyPool.from_url(args.proxy) if args.proxy else ProxyPool.from_auto()
    except ProxyUnavailableError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        sys.exit(1)

    dispatch_scrape_workers(
        tickers, pool,
        worker_fn=fetch_prices_worker,
        label="prices",
        workers=args.workers,
        force=args.force,
        extra_kwargs={"interval": MAGIC["price"]["interval"]},
    )


if __name__ == "__main__":
    main()
