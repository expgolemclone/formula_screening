#!/usr/bin/env python3
"""Fetch and cache stock prices from yfinance into the screening DB.

Usage:
    uv run python scripts/fetch_prices.py [--ticker TICKER ...] [--force]

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

from formula_screening.datasources.yfinance_price import RateLimitError, fetch_and_cache_prices
from formula_screening.db.repository import get_all_tickers
from formula_screening.db.schema import get_connection, init_db
from formula_screening.stealth import ProxyUnavailableError


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache stock prices")
    parser.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached <1 day")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    try:
        tickers = args.ticker if args.ticker else get_all_tickers(conn)
        print(f"Fetching prices for {len(tickers)} tickers...")
        result = fetch_and_cache_prices(conn, tickers, force=args.force)
        print(f"\nDone: {result['fetched']} fetched, {result['skipped']} skipped, {result['failed']} failed.")
    except (ProxyUnavailableError, RateLimitError) as e:
        print(f"ABORT: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
