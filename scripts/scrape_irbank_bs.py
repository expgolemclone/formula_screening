#!/usr/bin/env python3
"""Scrape detailed BS data from IRBank individual stock pages.

Usage:
    uv run python scripts/scrape_irbank_bs.py [--ticker 7203 3003] [--years 1]

Fetches the /bs page for each ticker, parses Google Charts data embedded
in the JavaScript, and upserts detailed balance-sheet items into the DB.

Uses the existing ProxyPool / create_session infrastructure for stealth.
"""

from __future__ import annotations

import argparse
import functools
import random
import sys
from pathlib import Path

# Ensure the project package is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.datasources.irbank_bs import build_bs_rows, fetch_bs_html
from formula_screening.db.repository import get_all_tickers, upsert_financial_items_bulk
from formula_screening.db.schema import get_connection, init_db
from formula_screening.stealth import ProxyPool, random_delay

print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape IRBank BS pages")
    parser.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    parser.add_argument("--years", type=int, default=1, help="Store most recent N years (default: 1)")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    parser.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    parser.add_argument("--interval", type=float, default=3.0, help="Min seconds between requests (default: 3.0)")
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
    ok = 0
    skip = 0
    fail = 0

    print(f"Scraping BS for {total} tickers (years={args.years})")

    for count, ticker in enumerate(tickers, 1):
        # Skip if data exists and not forcing
        if not args.force:
            existing = conn.execute(
                "SELECT 1 FROM financial_items WHERE ticker = ? AND source = 'irbank_bs' LIMIT 1",
                (ticker,),
            ).fetchone()
            if existing:
                skip += 1
                continue

        print(f"[{count}/{total}] {ticker}", end=" ")

        html = fetch_bs_html(ticker, pool)
        if html is None:
            print("FAILED")
            fail += 1
            continue

        rows = build_bs_rows(ticker, html, years=args.years)

        if rows:
            upsert_financial_items_bulk(conn, rows)
            conn.commit()
            print(f"OK ({len(rows)} items)")
            ok += 1
        else:
            print("NO DATA")
            fail += 1

        random_delay(args.interval, args.interval + 3.0)

    print(f"\nDone: {ok} scraped, {skip} skipped, {fail} failed.")
    conn.close()


if __name__ == "__main__":
    main()
