#!/usr/bin/env python3
"""Scrape detailed BS data from IRBank individual stock pages.

Usage:
    uv run python scripts/scrape_irbank_bs.py [--ticker 7203 3003] [--years 1]

Fetches the /bs page for each ticker, parses Google Charts data embedded
in the JavaScript, and upserts detailed balance-sheet items into the DB.

Uses the existing ProxyPool / create_session infrastructure for stealth.
Supports parallel scraping with multiple proxy sub-pools (--workers).
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
from formula_screening.datasources.irbank_bs import scrape_bs_worker
from formula_screening.db.repository import get_all_tickers
from formula_screening.db.schema import get_connection, init_db
from formula_screening.stealth import ProxyPool


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape IRBank BS pages")
    parser.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    _scrape = MAGIC["scrape"]
    parser.add_argument("--years", type=int, default=_scrape["bs_years"], help=f"Store most recent N years (default: {_scrape['bs_years']})")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    parser.add_argument("--workers", type=int, default=_scrape["workers"], help=f"Number of parallel workers (default: {_scrape['workers']})")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    if args.ticker:
        tickers = args.ticker
    else:
        tickers = get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
            sys.exit(1)

    conn.close()

    if args.proxy:
        pool = ProxyPool.from_url(args.proxy)
    else:
        pool = ProxyPool.from_auto()

    dispatch_scrape_workers(
        tickers, pool,
        worker_fn=scrape_bs_worker,
        label="BS",
        workers=args.workers,
        force=args.force,
        extra_kwargs={"years": args.years},
    )


if __name__ == "__main__":
    main()
