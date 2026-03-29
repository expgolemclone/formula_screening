"""CLI entry point for the screening tool."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from formula_screening.db.schema import get_connection, init_db
from formula_screening.log import setup_logging


def _cmd_import_irbank(args: argparse.Namespace) -> None:
    from formula_screening.config import DATA_DIR
    from formula_screening.datasources.irbank import import_irbank_json

    data_dir = Path(args.dir) if args.dir else DATA_DIR / "irbank"
    if not data_dir.is_dir():
        print(f"IR BANK data directory not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        years = args.years if args.years else None
        total = import_irbank_json(conn, data_dir, years=years)
        print(f"{total} financial items imported.")
    finally:
        conn.close()


def _resolve_proxy_pool(args: argparse.Namespace):  # noqa: ANN205
    """Build a ProxyPool from CLI args."""
    from formula_screening.stealth import ProxyPool

    if args.proxy:
        return ProxyPool.from_url(args.proxy)
    if args.no_proxy:
        return ProxyPool.direct()
    # Default: auto-proxy
    return ProxyPool.from_auto()


def _cmd_fetch_prices(args: argparse.Namespace) -> None:
    from formula_screening.datasources.yfinance_price import fetch_and_cache_prices
    from formula_screening.db.repository import get_all_tickers

    pool = _resolve_proxy_pool(args)
    conn = get_connection()
    try:
        tickers = args.ticker if args.ticker else get_all_tickers(conn)
        print(f"Fetching prices for {len(tickers)} tickers...")
        result = fetch_and_cache_prices(conn, tickers, force=args.force, pool=pool)
        print(f"\nDone: {result['fetched']} fetched, {result['skipped']} skipped, {result['failed']} failed.")
    finally:
        conn.close()


def _cmd_scrape_bs(args: argparse.Namespace) -> None:
    import concurrent.futures
    import random
    import threading

    from formula_screening.datasources.irbank_bs import scrape_bs_worker
    from formula_screening.db.repository import get_all_tickers

    pool = _resolve_proxy_pool(args)
    conn = get_connection()
    try:
        tickers = args.ticker if args.ticker else get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
            sys.exit(1)
    finally:
        conn.close()

    tickers = list(tickers)
    random.shuffle(tickers)
    total = len(tickers)
    workers = min(args.workers, total) or 1

    print(f"Scraping BS for {total} tickers (years={args.years}, workers={workers})")

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
                interval=3.0,
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


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening.screener import run_screening

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        start = time.monotonic()
        hits = run_screening(conn, strategy_path)
        elapsed = time.monotonic() - start

        if not hits:
            print("No stocks matched the screening criteria.")
            return

        # Display results as a table
        _print_table(hits)
        print(f"\n{len(hits)} stocks matched ({elapsed:.1f}s)")

        if args.output:
            _write_csv(hits, Path(args.output))
            print(f"Results written to {args.output}")
    finally:
        conn.close()


def _print_table(hits: list[dict]) -> None:
    """Print screening results as a formatted table to stdout."""
    headers = ["Ticker", "Name", "Price", "PER", "PBR", "ROE", "Div%"]
    rows = []
    for s in hits:
        m = s.get("metrics", {})
        rows.append([
            s["ticker"],
            (s["name"] or "")[:20],
            f'{s["price"]:.0f}' if s["price"] else "-",
            f'{m["per"]:.1f}' if m.get("per") else "-",
            f'{m["pbr"]:.2f}' if m.get("pbr") else "-",
            f'{m["roe"]:.1f}' if m.get("roe") else "-",
            f'{m["dividend_yield"]:.2f}' if m.get("dividend_yield") else "-",
        ])

    widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)

    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def _write_csv(hits: list[dict], path: Path) -> None:
    """Write screening results to a CSV file."""
    fieldnames = ["ticker", "name", "price", "per", "pbr", "roe", "dividend_yield"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in hits:
            m = s.get("metrics", {})
            writer.writerow({
                "ticker": s["ticker"],
                "name": s["name"],
                "price": s["price"],
                "per": m.get("per"),
                "pbr": m.get("pbr"),
                "roe": m.get("roe"),
                "dividend_yield": m.get("dividend_yield"),
            })


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="formula_screening",
        description="Screen Japanese stocks with user-defined Python formulas.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    # import-irbank
    p_import = sub.add_parser("import-irbank", help="Import IR BANK JSON data into DB")
    p_import.add_argument("--dir", help="IR BANK data directory (default: data/irbank)")
    p_import.add_argument("--years", type=int, help="Only import the most recent N years")

    # fetch-prices
    p_prices = sub.add_parser("fetch-prices", help="Fetch and cache stock prices from yfinance")
    p_prices.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    p_prices.add_argument("--force", action="store_true", help="Re-fetch even if cached <1 day")
    p_prices.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_prices.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # scrape-bs
    p_bs = sub.add_parser("scrape-bs", help="Scrape detailed BS from IRBank individual pages")
    p_bs.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_bs.add_argument("--years", type=int, default=1, help="Store most recent N years (default: 1)")
    p_bs.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_bs.add_argument("--workers", type=int, default=100, help="Number of parallel workers (default: 100)")
    p_bs.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_bs.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # screen
    p_screen = sub.add_parser("screen", help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    cmds = {
        "import-irbank": _cmd_import_irbank,
        "fetch-prices": _cmd_fetch_prices,
        "scrape-bs": _cmd_scrape_bs,
        "screen": _cmd_screen,
    }
    cmds[args.command](args)
