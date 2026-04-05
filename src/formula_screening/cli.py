"""CLI entry point for the screening tool."""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from collections.abc import Callable

from formula_screening.config import MAGIC
from formula_screening.db.schema import get_connection, init_db
from formula_screening.fmt import display_width, ljust, truncate
from formula_screening.log import setup_logging

if TYPE_CHECKING:
    from formula_screening.stealth import ProxyPool

_ExtraColsFn = Callable[[dict], list[tuple[str, str]]]


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


def _resolve_proxy_pool(args: argparse.Namespace) -> ProxyPool:
    """Build a ProxyPool from CLI args."""
    from formula_screening.stealth import ProxyPool

    if args.proxy:
        return ProxyPool.from_url(args.proxy)
    target: int = getattr(args, "target_proxies", MAGIC["proxy"]["target_count"])
    check_sites: int = getattr(args, "check_sites", MAGIC["proxy"]["quality_check_count"])
    return ProxyPool.from_auto(target_count=target, quality_check_count=check_sites)


def _cmd_fetch_prices(args: argparse.Namespace) -> None:
    from formula_screening.datasources.yfinance_price import (
        RateLimitError,
        fetch_and_cache_prices,
    )
    from formula_screening.db.repository import get_all_tickers

    pool = _resolve_proxy_pool(args)
    conn = get_connection()
    try:
        tickers = args.ticker if args.ticker else get_all_tickers(conn)
        print(f"Fetching prices for {len(tickers)} tickers...")
        result = fetch_and_cache_prices(conn, tickers, force=args.force, pool=pool, workers=args.workers)
        print(f"\nDone: {result['fetched']} fetched, {result['skipped']} skipped, {result['failed']} failed.")
    except RateLimitError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def dispatch_scrape_workers(
    tickers: list[str],
    pool: ProxyPool,
    *,
    worker_fn: Callable[..., None],
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
    proxy_count: int = pool.size
    requested_workers = workers
    if proxy_count > 0:
        workers = min(workers, proxy_count)
    workers = min(workers, total) or 1

    worker_parts = [f"workers={workers}"]
    if requested_workers != workers:
        worker_parts.append(f"requested={requested_workers}")
    if proxy_count > 0:
        worker_parts.append(f"proxies={proxy_count}")
    print(f"Scraping {label} for {total} tickers ({', '.join(worker_parts)})")

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
            try:
                f.result()
            except Exception:
                logging.getLogger("formula_screening.cli").warning(
                    "Worker raised an exception", exc_info=True,
                )

    print(f"\nDone: {stats['ok']} scraped, {stats['skip']} skipped, {stats['fail']} failed.")
    return stats


def _run_scrape_workers(
    args: argparse.Namespace,
    *,
    worker_fn: Callable[..., None],
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

    refresh_stale_sources(changed, proxy_pool=pool, workers=args.workers)
    save_hashes(compute_hashes())
    print("\nRefresh complete.")


def _cmd_probe_proxies(args: argparse.Namespace) -> None:
    from formula_screening.stealth import ProxyPool, clear_failure_cache

    if args.clear_legacy_cache:
        removed, remaining = clear_failure_cache(reasons={"legacy"})
        print(f"Removed {removed} legacy failure-cache entries ({remaining} remaining).")

    if args.proxy:
        pool = ProxyPool.from_url(args.proxy)
    else:
        pool = ProxyPool.from_auto(
            target_count=args.target_proxies,
            quality_check_count=args.check_sites,
        )
    print(f"Live proxies ready: {pool.size}")
    print(f"Current proxy: {pool.get() or 'none'}")


def _cmd_clear_failure_cache(args: argparse.Namespace) -> None:
    from formula_screening.stealth import (
        clear_failure_cache,
        failure_cache_reason_counts,
    )

    def render_counts(counts: dict[str, int]) -> str:
        return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "empty"

    before = failure_cache_reason_counts()
    total_before = sum(before.values())
    print(f"Failure cache before: {total_before} ({render_counts(before)})")

    if not args.all and not args.reason:
        print("Nothing cleared. Pass --reason REASON (repeatable) or --all.")
        return

    reasons = None if args.all else set(args.reason)
    removed, remaining = clear_failure_cache(reasons=reasons)
    after = failure_cache_reason_counts()
    print(f"Removed {removed} entries.")
    print(f"Failure cache after: {remaining} ({render_counts(after)})")


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening.cache_invalidation import ensure_data_available
    from formula_screening.screener import load_strategy, run_screening

    ensure_data_available(get_proxy_pool=lambda: _resolve_proxy_pool(args))

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    strategy_mod = load_strategy(strategy_path)
    extra_cols_fn: _ExtraColsFn | None = getattr(strategy_mod, "columns", None)

    conn = get_connection()
    try:
        start = time.monotonic()
        hits = run_screening(conn, strategy_path, workers=args.workers)
        elapsed = time.monotonic() - start

        if not hits:
            print("No stocks matched the screening criteria.")
            return

        sort_key_fn = getattr(strategy_mod, "sort_key", None)
        if sort_key_fn is not None:
            hits.sort(key=sort_key_fn, reverse=True)
        else:
            hits.sort(key=lambda s: s.get("metrics", {}).get("net_cash_ratio") or 0, reverse=True)

        # Display results as a table
        _print_table(hits, extra_cols_fn=extra_cols_fn)
        print(f"\n{len(hits)} stocks matched ({elapsed:.1f}s)")

        if args.output:
            _write_csv(hits, Path(args.output), extra_cols_fn=extra_cols_fn)
            print(f"Results written to {args.output}")

        if args.open is not None:
            to_open = hits[:args.open] if args.open > 0 else hits
            _open_shikiho(to_open)
    finally:
        conn.close()


_SHIKIHO_URL_TEMPLATE = "https://shikiho.toyokeizai.net/stocks/{ticker}/shikiho"


def _open_shikiho(hits: list[dict]) -> None:
    """Open all hit tickers on Shikiho Online via qutebrowser (fallback: default browser)."""
    import shutil
    import subprocess

    urls: list[str] = [_SHIKIHO_URL_TEMPLATE.format(ticker=s["ticker"]) for s in hits]
    qb: str | None = shutil.which("qutebrowser")
    if qb:
        subprocess.Popen([qb, *urls])
    else:
        import webbrowser

        for url in urls:
            webbrowser.open(url)
    print(f"Opened {len(hits)} tickers in browser.")


def _print_table(
    hits: list[dict],
    *,
    extra_cols_fn: _ExtraColsFn | None = None,
) -> None:
    """Print screening results as a formatted table to stdout."""
    headers = ["Ticker", "Name", "Price", "NC_Ratio", "PER", "PBR", "Div%"]
    rows: list[list[str]] = []

    for s in hits:
        m = s.get("metrics", {})
        row = [
            s["ticker"],
            truncate(s["name"] or "", 20),
            f'{s["price"]:.0f}' if s["price"] else "-",
            f'{m["net_cash_ratio"]:.2f}' if m.get("net_cash_ratio") else "-",
            f'{m["per"]:.1f}' if m.get("per") else "-",
            f'{m["pbr"]:.2f}' if m.get("pbr") else "-",
            f'{m["dividend_yield"]:.2f}' if m.get("dividend_yield") else "-",
        ]
        if extra_cols_fn is not None:
            extra = extra_cols_fn(s)
            if not rows:
                headers.extend(h for h, _ in extra)
            row.extend(v for _, v in extra)
        rows.append(row)

    widths = [
        max(display_width(h), max((display_width(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]

    sep = "  "
    print(sep.join(ljust(h, w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))
    for row in rows:
        print(sep.join(ljust(cell, w) for cell, w in zip(row, widths)))


def _write_csv(
    hits: list[dict],
    path: Path,
    *,
    extra_cols_fn: _ExtraColsFn | None = None,
) -> None:
    """Write screening results to a CSV file."""
    fieldnames = ["ticker", "name", "price", "net_cash_ratio", "per", "pbr", "dividend_yield"]

    extra_headers: list[str] = []
    if extra_cols_fn is not None and hits:
        extra_headers = [h for h, _ in extra_cols_fn(hits[0])]
        fieldnames.extend(extra_headers)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in hits:
            m = s.get("metrics", {})
            row: dict[str, object] = {
                "ticker": s["ticker"],
                "name": s["name"],
                "price": s["price"],
                "net_cash_ratio": m.get("net_cash_ratio"),
                "per": m.get("per"),
                "pbr": m.get("pbr"),
                "dividend_yield": m.get("dividend_yield"),
            }
            if extra_cols_fn is not None:
                for header, value in extra_cols_fn(s):
                    row[header] = value
            writer.writerow(row)


def main() -> None:
    from formula_screening.stealth import ProxyUnavailableError

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
    p_prices.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    p_prices.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    # scrape-bs
    p_bs = sub.add_parser("scrape-bs", help="Scrape detailed BS from IRBank individual pages")
    p_bs.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_bs.add_argument("--years", type=int, default=MAGIC["scrape"]["bs_years"], help="Store most recent N years")
    p_bs.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_bs.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")
    p_bs.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_bs.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    p_bs.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    # scrape-forecast
    p_fc = sub.add_parser("scrape-forecast", help="Scrape forecast data from IRBank /results pages")
    p_fc.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_fc.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_fc.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")
    p_fc.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_fc.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    p_fc.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    # refresh
    p_refresh = sub.add_parser("refresh", help="Check scraper hash changes, invalidate stale cache, and re-fetch")
    p_refresh.add_argument("--force", action="store_true", help="Force re-fetch all sources regardless of hash")
    p_refresh.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel scrape workers for refresh")
    p_refresh.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_refresh.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    p_refresh.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    # probe-proxies
    p_probe = sub.add_parser("probe-proxies", help="Probe public proxies without touching screening data")
    p_probe.add_argument("--proxy", help="Specific HTTP proxy URL to validate (e.g. http://host:port)")
    p_probe.add_argument("--target-proxies", type=int, default=1, help="Number of proxies to acquire")
    p_probe.add_argument("--check-sites", type=int, default=0, help="Number of quality sites each proxy must pass")
    p_probe.add_argument("--clear-legacy-cache", action="store_true", help="Remove only legacy failure-cache entries before probing")

    # clear-failure-cache
    from formula_screening.stealth import failure_cache_reasons

    p_clear = sub.add_parser("clear-failure-cache", help="Clear proxy failure-cache entries by reason")
    p_clear.add_argument("--reason", action="append", choices=failure_cache_reasons(), help="Failure reason to remove; repeat to remove multiple reasons")
    p_clear.add_argument("--all", action="store_true", help="Remove all active failure-cache entries")

    # screen
    p_screen = sub.add_parser("screen", help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")
    p_screen.add_argument("--open", nargs="?", type=int, const=0, default=None,
                           help="Open top N hits on Shikiho Online (omit N for all)")
    p_screen.add_argument("--workers", type=int, default=MAGIC["screening"]["workers"], help="Number of parallel screening workers")
    p_screen.add_argument("--proxy", help="HTTP proxy URL (e.g. http://host:port)")
    p_screen.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    p_screen.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    # Auto-check scraper hashes before any command (refresh handles its own)
    if args.command not in {"refresh", "probe-proxies", "clear-failure-cache"}:
        from formula_screening.cache_invalidation import check_and_invalidate

        check_and_invalidate(verbose=args.verbose)

    cmds = {
        "import-irbank": _cmd_import_irbank,
        "fetch-prices": _cmd_fetch_prices,
        "scrape-bs": _cmd_scrape_bs,
        "scrape-forecast": _cmd_scrape_forecast,
        "refresh": _cmd_refresh,
        "probe-proxies": _cmd_probe_proxies,
        "clear-failure-cache": _cmd_clear_failure_cache,
        "screen": _cmd_screen,
    }
    try:
        cmds[args.command](args)
    except ProxyUnavailableError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        sys.exit(1)
