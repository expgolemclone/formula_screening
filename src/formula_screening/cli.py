"""CLI entry point for the screening tool."""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from formula_screening.config import MAGIC
from formula_screening.db.schema import get_connection, init_db
from formula_screening.fmt import display_width, ljust, truncate
from formula_screening.log import setup_logging


def _cmd_import_irbank(args: argparse.Namespace) -> None:
    from formula_screening.config import IRBANK_DIR
    from formula_screening.datasources.irbank import import_irbank_json

    data_dir = Path(args.dir) if args.dir else IRBANK_DIR
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
        result = fetch_and_cache_prices(conn, tickers, force=args.force, pool=pool, workers=args.workers)
        print(f"\nDone: {result['fetched']} fetched, {result['skipped']} skipped, {result['failed']} failed.")
    finally:
        conn.close()


def dispatch_scrape_workers(
    tickers: list[str],
    pool: object,
    *,
    worker_fn: object,
    label: str,
    workers: int = MAGIC["scrape"]["workers"],
    force: bool = False,
    extra_kwargs: dict | None = None,
) -> dict[str, int]:
    """Dispatch parallel scrape workers and return stats.

    Shared by CLI subcommands and cache_invalidation.refresh_stale_sources.
    """
    import concurrent.futures
    import random
    import threading

    tickers = list(tickers)
    random.shuffle(tickers)
    total = len(tickers)
    workers = min(workers, total) or 1

    print(f"Scraping {label} for {total} tickers (workers={workers})")

    sub_pools = pool.split(workers)
    chunks: list[list[str]] = [[] for _ in range(workers)]
    for i, ticker in enumerate(tickers):
        chunks[i % workers].append(ticker)

    stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0}
    stats_lock = threading.Lock()
    counter = [0]

    kwargs = {
        "interval": MAGIC["scrape"]["interval"],
        "force": force,
        "stats": stats,
        "stats_lock": stats_lock,
        "total": total,
        "counter": counter,
        **(extra_kwargs or {}),
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(worker_fn, chunk, sub_pool, **kwargs)
            for chunk, sub_pool in zip(chunks, sub_pools)
        ]
        concurrent.futures.wait(futures)
        for f in futures:
            f.result()

    print(f"\nDone: {stats['ok']} scraped, {stats['skip']} skipped, {stats['fail']} failed.")
    return stats


def _run_scrape_workers(
    args: argparse.Namespace,
    *,
    worker_fn: object,
    label: str,
    extra_kwargs: dict | None = None,
) -> None:
    """CLI wrapper: resolve args then delegate to dispatch_scrape_workers."""
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

    dispatch_scrape_workers(
        tickers, pool,
        worker_fn=worker_fn,
        label=label,
        workers=args.workers,
        force=args.force,
        extra_kwargs=extra_kwargs,
    )


def _cmd_scrape_bs(args: argparse.Namespace) -> None:
    from formula_screening.datasources.irbank_bs import scrape_bs_worker

    _run_scrape_workers(
        args,
        worker_fn=scrape_bs_worker,
        label="BS",
        extra_kwargs={"years": args.years},
    )


def _cmd_scrape_forecast(args: argparse.Namespace) -> None:
    from formula_screening.datasources.irbank_forecast import scrape_forecast_worker

    _run_scrape_workers(
        args,
        worker_fn=scrape_forecast_worker,
        label="forecast",
    )


def _cmd_refresh(args: argparse.Namespace) -> None:
    from formula_screening.cache_invalidation import (
        check_and_invalidate,
        compute_hashes,
        refresh_stale_sources,
        save_hashes,
    )

    pool = _resolve_proxy_pool(args)

    if args.force:
        current = compute_hashes()
        from formula_screening.cache_invalidation import (
            _TRACKED_FILES,
            invalidate_cache,
        )

        all_files = sorted(_TRACKED_FILES & set(current))
        print("Force refresh — invalidating all caches...")
        deleted = invalidate_cache(all_files)
        for desc, count in deleted.items():
            print(f"  {desc}: {count} rows")
        changed = all_files
    else:
        changed = check_and_invalidate(verbose=args.verbose)

    if not changed:
        print("Cache is up to date. Nothing to refresh.")
        return

    refresh_stale_sources(changed, proxy_pool=pool)
    save_hashes(compute_hashes())
    print("\nRefresh complete.")


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening.cache_invalidation import ensure_data_available
    from formula_screening.screener import run_screening
    from formula_screening.stealth import ProxyPool

    ensure_data_available(proxy_pool=ProxyPool.from_auto())

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

        # Sort by net_cash_ratio descending (most undervalued first)
        hits.sort(key=lambda s: s.get("metrics", {}).get("net_cash_ratio") or 0, reverse=True)

        # Display results as a table
        _print_table(hits)
        print(f"\n{len(hits)} stocks matched ({elapsed:.1f}s)")

        if args.output:
            _write_csv(hits, Path(args.output))
            print(f"Results written to {args.output}")

        if args.open:
            _open_shikiho(hits)
    finally:
        conn.close()


_SHIKIHO_URL_TEMPLATE = "https://shikiho.toyokeizai.net/stocks/{ticker}/shikiho"


def _open_shikiho(hits: list[dict]) -> None:
    """Open all hit tickers on Shikiho Online via qutebrowser (fallback: default browser)."""
    import shutil
    import subprocess

    qb = shutil.which("qutebrowser")
    if qb:
        subprocess.Popen([qb])
        print("Waiting 10s for qutebrowser to start...")
        time.sleep(10)
        for s in hits:
            url = _SHIKIHO_URL_TEMPLATE.format(ticker=s["ticker"])
            subprocess.Popen([qb, url])
    else:
        import webbrowser

        for s in hits:
            webbrowser.open(_SHIKIHO_URL_TEMPLATE.format(ticker=s["ticker"]))
    print(f"Opened {len(hits)} tickers in browser.")


def _print_table(hits: list[dict]) -> None:
    """Print screening results as a formatted table to stdout."""
    headers = ["Ticker", "Name", "Price", "NC_Ratio", "PER", "PBR", "ROE", "Div%"]
    rows = []
    for s in hits:
        m = s.get("metrics", {})
        rows.append([
            s["ticker"],
            truncate(s["name"] or "", 20),
            f'{s["price"]:.0f}' if s["price"] else "-",
            f'{m["net_cash_ratio"]:.2f}' if m.get("net_cash_ratio") else "-",
            f'{m["per"]:.1f}' if m.get("per") else "-",
            f'{m["pbr"]:.2f}' if m.get("pbr") else "-",
            f'{m["roe"]:.1f}' if m.get("roe") else "-",
            f'{m["dividend_yield"]:.2f}' if m.get("dividend_yield") else "-",
        ])

    widths = [
        max(display_width(h), max((display_width(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]

    sep = "  "
    print(sep.join(ljust(h, w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))
    for row in rows:
        print(sep.join(ljust(cell, w) for cell, w in zip(row, widths)))


def _write_csv(hits: list[dict], path: Path) -> None:
    """Write screening results to a CSV file."""
    fieldnames = ["ticker", "name", "price", "net_cash_ratio", "per", "pbr", "roe", "dividend_yield"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in hits:
            m = s.get("metrics", {})
            writer.writerow({
                "ticker": s["ticker"],
                "name": s["name"],
                "price": s["price"],
                "net_cash_ratio": m.get("net_cash_ratio"),
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
    p_prices.add_argument("--workers", type=int, default=MAGIC["price"]["shares_workers"], help="Number of parallel workers for shares fetch")
    p_prices.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_prices.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # scrape-bs
    p_bs = sub.add_parser("scrape-bs", help="Scrape detailed BS from IRBank individual pages")
    p_bs.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_bs.add_argument("--years", type=int, default=MAGIC["scrape"]["bs_years"], help="Store most recent N years")
    p_bs.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_bs.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")
    p_bs.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_bs.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # scrape-forecast
    p_fc = sub.add_parser("scrape-forecast", help="Scrape forecast data from IRBank /results pages")
    p_fc.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_fc.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_fc.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")
    p_fc.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_fc.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # refresh
    p_refresh = sub.add_parser("refresh", help="Check scraper hash changes, invalidate stale cache, and re-fetch")
    p_refresh.add_argument("--force", action="store_true", help="Force re-fetch all sources regardless of hash")
    p_refresh.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_refresh.add_argument("--no-proxy", action="store_true", help="Disable auto-proxy (direct connection)")

    # screen
    p_screen = sub.add_parser("screen", help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")
    p_screen.add_argument("--open", action="store_true", help="Open all hits on Shikiho Online in browser")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    # Auto-check scraper hashes before any command (refresh handles its own)
    if args.command != "refresh":
        from formula_screening.cache_invalidation import check_and_invalidate

        check_and_invalidate(verbose=args.verbose)

    cmds = {
        "import-irbank": _cmd_import_irbank,
        "fetch-prices": _cmd_fetch_prices,
        "scrape-bs": _cmd_scrape_bs,
        "scrape-forecast": _cmd_scrape_forecast,
        "refresh": _cmd_refresh,
        "screen": _cmd_screen,
    }
    cmds[args.command](args)
