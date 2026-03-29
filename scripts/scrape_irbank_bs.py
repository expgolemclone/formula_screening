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
import concurrent.futures
import functools
import random
import sys
import threading
from pathlib import Path

# Ensure the project package is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.datasources.irbank_bs import scrape_bs_worker
from formula_screening.db.repository import get_all_tickers
from formula_screening.db.schema import get_connection, init_db
from formula_screening.stealth import ProxyPool

print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape IRBank BS pages")
    parser.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    parser.add_argument("--years", type=int, default=1, help="Store most recent N years (default: 1)")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    parser.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    parser.add_argument("--interval", type=float, default=3.0, help="Min seconds between requests (default: 3.0)")
    parser.add_argument("--workers", type=int, default=100, help="Number of parallel workers (default: 100)")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    # Resolve tickers
    if args.ticker:
        tickers = args.ticker
    else:
        tickers = get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
            sys.exit(1)

    conn.close()

    # Resolve proxy
    if args.proxy:
        pool = ProxyPool.from_url(args.proxy)
    elif args.no_proxy:
        pool = ProxyPool.direct()
    else:
        pool = ProxyPool.from_auto()

    # Shuffle to distribute requests
    tickers = list(tickers)
    random.shuffle(tickers)

    total = len(tickers)
    workers = min(args.workers, total) or 1

    print(f"Scraping BS for {total} tickers (years={args.years}, workers={workers})")

    # Split proxies and tickers among workers
    sub_pools = pool.split(workers)
    chunks: list[list[str]] = [[] for _ in range(workers)]
    for i, ticker in enumerate(tickers):
        chunks[i % workers].append(ticker)

    stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0}
    stats_lock = threading.Lock()
    counter = [0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                scrape_bs_worker,
                chunk,
                sub_pool,
                years=args.years,
                interval=args.interval,
                force=args.force,
                stats=stats,
                stats_lock=stats_lock,
                total=total,
                counter=counter,
            )
            for chunk, sub_pool in zip(chunks, sub_pools)
        ]
        concurrent.futures.wait(futures)
        for f in futures:
            f.result()

    print(f"\nDone: {stats['ok']} scraped, {stats['skip']} skipped, {stats['fail']} failed.")


if __name__ == "__main__":
    main()
