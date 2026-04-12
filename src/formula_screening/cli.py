"""CLI entry point for the screening tool."""
from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from collections.abc import Callable

from formula_screening.config import CLI_DEFAULTS, MAGIC
from formula_screening.db.schema import get_connection, init_db
from formula_screening.fmt import display_width, ljust, truncate
from formula_screening.log import setup_logging
from formula_screening.screen_output import (
    LinkCell,
    ScreenColumn,
    ScreenColumnValue,
    build_osc8_hyperlink,
    build_sikiho_url,
    supports_osc8_hyperlinks,
)

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

_ExtraColsFn = Callable[[dict], list[ScreenColumn]]
_TRANSIENT_PROXY_FAILURE_REASONS: set[str] = {"tcp_unreachable", "anon_unreachable"}
logger = logging.getLogger("formula_screening.cli")


def _cmd_import_irbank(args: argparse.Namespace) -> None:
    from formula_screening.config import IRBANK_DIR
    from formula_screening.scrape.irbank import import_irbank_json

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
    """Build a ProxyPool from CLI args.

    ``--proxy`` accepts three forms:

    - ``"direct"`` (default): bypass the proxy pool and use a direct connection
    - ``"auto"``: auto-fetch public proxies via :meth:`ProxyPool.from_auto`
    - any URL (``http://``/``https://``/``socks5://``): a single user proxy
    """
    from formula_screening.stealth import ProxyPool, clear_failure_cache

    if args.proxy_file:
        return ProxyPool.from_file(Path(args.proxy_file))
    proxy_value: str = args.proxy
    if proxy_value == "direct":
        return ProxyPool([], direct=True)
    if proxy_value != "auto":
        return ProxyPool.from_url(proxy_value)
    if _should_clear_transient_proxy_failures(args):
        removed, remaining = clear_failure_cache(reasons=_TRANSIENT_PROXY_FAILURE_REASONS)
        logger.info(
            "Cleared %d transient proxy failure-cache entries (%d remaining)",
            removed,
            remaining,
        )
    target: int = args.target_proxies
    check_sites: int = args.check_sites
    return ProxyPool.from_auto(target_count=target, quality_check_count=check_sites)


def _should_clear_transient_proxy_failures(args: argparse.Namespace) -> bool:
    """Return True when a command should reset transient proxy failures.

    Only applies to the ``--proxy auto`` path; direct-mode and user-specified
    proxies never touch the failure cache.
    """
    if args.proxy != "auto":
        return False
    if args.command in {"refresh", "screen"}:
        return True
    if args.command in {"fetch-prices", "fetch-shares", "scrape-bs", "scrape-forecast"}:
        return not bool(args.ticker)
    return False


def _start_browser_service() -> BrowserService:
    """Create and start a BrowserService instance."""
    from formula_screening.browser import BrowserService

    browser: BrowserService = BrowserService()
    browser.start()
    return browser


def _cmd_fetch_prices(args: argparse.Namespace) -> None:
    from formula_screening.db.repository import get_all_tickers
    from formula_screening.worker import fetch_prices_stooq

    conn = get_connection()
    try:
        tickers: list[str] = args.ticker if args.ticker else get_all_tickers(conn)
    finally:
        conn.close()

    if not tickers:
        print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
        sys.exit(1)

    _cached_browser: BrowserService | None = None

    def _get_browser() -> BrowserService:
        nonlocal _cached_browser
        if _cached_browser is None:
            _cached_browser = _start_browser_service()
        return _cached_browser

    try:
        fetch_prices_stooq(tickers, get_browser=_get_browser, force=args.force)
    finally:
        if _cached_browser is not None:
            _cached_browser.shutdown()



def dispatch_workers(
    tickers: list[str],
    pool: ProxyPool,
    *,
    worker_fn: Callable[..., None],
    label: str,
    workers: int = MAGIC["scrape"]["workers"],
    force: bool = False,
    extra_kwargs: dict | None = None,
) -> dict[str, int]:
    """Dispatch parallel workers and return stats."""
    import concurrent.futures
    import random
    import threading

    from formula_screening.stealth import ProxyUnavailableError

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
    if pool.direct:
        worker_parts.append("proxies=direct")
    elif proxy_count > 0:
        worker_parts.append(f"proxies={proxy_count}")
    print(f"Fetching {label} for {total} tickers ({', '.join(worker_parts)})")

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
    }
    if extra_kwargs is not None:
        kwargs.update(extra_kwargs)

    proxy_error: Exception | None = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(worker_fn, chunk, sub_pool, **kwargs)
            for chunk, sub_pool in zip(chunks, sub_pools)
        ]
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except ProxyUnavailableError as exc:
                proxy_error = exc
                for pending in futures:
                    if pending is not f:
                        pending.cancel()
                break
            except Exception:
                logger.warning(
                    "Worker raised an exception", exc_info=True,
                )

    if proxy_error is not None:
        raise proxy_error

    print(f"\nDone: {stats['ok']} ok, {stats['skip']} skipped, {stats['fail']} failed.")
    return stats


def _run_scrape_workers(
    args: argparse.Namespace,
    *,
    worker_fn: Callable[..., None],
    label: str,
    extra_kwargs: dict | None = None,
    skip_filter_fn: Callable[[sqlite3.Connection, list[str]], set[str]] | None = None,
) -> None:
    """CLI wrapper: resolve args then delegate to :func:`dispatch_workers`."""
    from formula_screening.db.repository import get_all_tickers

    pool = _resolve_proxy_pool(args)
    conn = get_connection()
    try:
        tickers: list[str] = args.ticker if args.ticker else get_all_tickers(conn)
        if not tickers:
            print("No tickers in DB. Run import-irbank first.", file=sys.stderr)
            sys.exit(1)

        if not args.force and skip_filter_fn is not None:
            existing: set[str] = skip_filter_fn(conn, tickers)
            skipped: int = len(existing & set(tickers))
            if skipped > 0:
                tickers = [t for t in tickers if t not in existing]
                print(f"Skipping {skipped} tickers (already have data)")
    finally:
        conn.close()

    if not tickers:
        print("All tickers already have data. Use --force to re-fetch.")
        return

    dispatch_workers(
        tickers, pool,
        worker_fn=worker_fn,
        label=label,
        workers=args.workers,
        force=args.force,
        extra_kwargs=extra_kwargs,
    )


def _cmd_scrape_bs(args: argparse.Namespace) -> None:
    from formula_screening.db.repository import get_existing_tickers
    from formula_screening.worker import scrape_bs_worker

    with _start_browser_service() as browser:
        _run_scrape_workers(
            args,
            worker_fn=scrape_bs_worker,
            label="BS",
            extra_kwargs={"years": args.years, "browser": browser},
            skip_filter_fn=lambda conn, tickers: get_existing_tickers(conn, "irbank_bs"),
        )


def _cmd_scrape_forecast(args: argparse.Namespace) -> None:
    from formula_screening.db.repository import get_existing_tickers
    from formula_screening.worker import scrape_forecast_worker

    with _start_browser_service() as browser:
        _run_scrape_workers(
            args,
            worker_fn=scrape_forecast_worker,
            label="forecast",
            extra_kwargs={"browser": browser},
            skip_filter_fn=lambda conn, tickers: get_existing_tickers(conn, "irbank_forecast"),
        )


def _cmd_fetch_shares(args: argparse.Namespace) -> None:
    from formula_screening.db.repository import get_tickers_with_shares
    from formula_screening.worker import scrape_shares_worker

    _run_scrape_workers(
        args,
        worker_fn=scrape_shares_worker,
        label="shares",
        skip_filter_fn=lambda conn, tickers: get_tickers_with_shares(conn),
    )


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
    from formula_screening.bootstrap import DATA_SOURCES, ensure_data_available
    from formula_screening.screener import load_strategy, run_screening

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    strategy_mod = load_strategy(strategy_path)
    required_sources: list[str] | None = getattr(strategy_mod, "REQUIRED_SOURCES", None)

    _screen_browser: BrowserService | None = None

    def _get_screen_browser() -> BrowserService:
        nonlocal _screen_browser
        if _screen_browser is None:
            _screen_browser = _start_browser_service()
        return _screen_browser

    try:
        ensure_data_available(
            required_sources=required_sources if required_sources is not None else DATA_SOURCES,
            get_proxy_pool=lambda: _resolve_proxy_pool(args),
            get_browser=_get_screen_browser,
        )
    finally:
        if _screen_browser is not None:
            _screen_browser.shutdown()

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
            hits.sort(key=lambda s: s["metrics"].get("net_cash_ratio") or 0, reverse=True)

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


def _open_shikiho(hits: list[dict]) -> None:
    """Open all hit tickers on Shikiho Online via qutebrowser (fallback: default browser)."""
    urls: list[str] = [build_sikiho_url(str(s["ticker"])) for s in hits]
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
    hyperlinks_enabled = supports_osc8_hyperlinks(os.environ, sys.stdout.isatty())
    lines = _build_table_lines(
        hits,
        extra_cols_fn=extra_cols_fn,
        hyperlinks_enabled=hyperlinks_enabled,
    )
    print("\n".join(lines))


def _display_cell_text(value: ScreenColumnValue) -> str:
    """Return the visible label for a screen-output cell."""

    if isinstance(value, LinkCell):
        return value.label
    return value


def _render_table_cell(
    value: ScreenColumnValue,
    width: int,
    *,
    hyperlinks_enabled: bool,
) -> str:
    """Render a padded table cell, optionally wrapped in OSC 8."""

    label = _display_cell_text(value)
    padding = " " * max(width - display_width(label), 0)
    if not hyperlinks_enabled:
        return label + padding
    if isinstance(value, LinkCell):
        return build_osc8_hyperlink(label, value.url) + padding
    return label + padding


def _build_table_lines(
    hits: list[dict],
    *,
    extra_cols_fn: _ExtraColsFn | None = None,
    hyperlinks_enabled: bool,
) -> list[str]:
    """Build display lines for the screen-results table."""

    headers = ["Ticker", "Name", "Price", "NC_Ratio", "PER", "PBR", "Div%"]
    rows: list[list[ScreenColumnValue]] = []

    for stock in hits:
        metrics = stock["metrics"]
        row = [
            str(stock["ticker"]),
            truncate(str(stock["name"] or ""), 20),
            f'{stock["price"]:.0f}' if stock["price"] else "-",
            f'{metrics["net_cash_ratio"]:.2f}' if metrics.get("net_cash_ratio") else "-",
            f'{metrics["per"]:.1f}' if metrics.get("per") else "-",
            f'{metrics["pbr"]:.2f}' if metrics.get("pbr") else "-",
            f'{metrics["dividend_yield"]:.2f}' if metrics.get("dividend_yield") else "-",
        ]
        if extra_cols_fn is not None:
            extra = extra_cols_fn(stock)
            if not rows:
                headers.extend(header for header, _ in extra)
            row.extend(value for _, value in extra)
        rows.append(row)

    widths = [
        max(
            display_width(header),
            max((display_width(_display_cell_text(row[i])) for row in rows), default=0),
        )
        for i, header in enumerate(headers)
    ]

    sep = "  "
    lines = [sep.join(ljust(header, width) for header, width in zip(headers, widths))]
    lines.append(sep.join("-" * width for width in widths))
    for row in rows:
        lines.append(
            sep.join(
                _render_table_cell(cell, width, hyperlinks_enabled=hyperlinks_enabled)
                for cell, width in zip(row, widths)
            )
        )
    return lines


def _format_csv_cell(value: ScreenColumnValue) -> str:
    """Render a screen cell for CSV output."""

    if isinstance(value, LinkCell):
        return value.url
    return value


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
            m = s["metrics"]
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
                    row[header] = _format_csv_cell(value)
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

    _proxy_args = argparse.ArgumentParser(add_help=False)
    _proxy_args.add_argument(
        "--proxy",
        default="direct",
        help=(
            "Proxy mode: 'direct' (default, no proxy), 'auto' (fetch public proxies), "
            "or a proxy URL (http://host:port)"
        ),
    )
    _proxy_args.add_argument("--proxy-file", help="Path to proxy list file (host:port:user:pass per line)")
    _proxy_args.add_argument("--target-proxies", type=int, default=MAGIC["proxy"]["target_count"], help="Number of proxies to acquire")
    _proxy_args.add_argument("--check-sites", type=int, default=MAGIC["proxy"]["quality_check_count"], help="Number of sites each proxy must pass")

    # import-irbank
    p_import = sub.add_parser("import-irbank", help="Import IR BANK JSON data into DB")
    p_import.add_argument("--dir", help="IR BANK data directory (default: data/irbank)")
    p_import.add_argument("--years", type=int, help="Only import the most recent N years")

    # fetch-prices
    p_prices = sub.add_parser("fetch-prices", parents=[_proxy_args], help="Fetch and cache stock prices")
    p_prices.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    p_prices.add_argument("--force", action="store_true", help="Re-fetch even if cached <1 day")

    # scrape-bs
    _bs_defaults = CLI_DEFAULTS["scrape_bs"]
    p_bs = sub.add_parser("scrape-bs", parents=[_proxy_args], help="Scrape detailed BS from IRBank individual pages")
    p_bs.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_bs.add_argument("--years", type=int, default=MAGIC["scrape"]["bs_years"], help="Store most recent N years")
    p_bs.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_bs.add_argument("--workers", type=int, default=_bs_defaults["workers"], help="Number of parallel workers")
    p_bs.set_defaults(target_proxies=_bs_defaults["target_proxies"], check_sites=_bs_defaults["check_sites"])

    # scrape-forecast
    p_fc = sub.add_parser("scrape-forecast", parents=[_proxy_args], help="Scrape forecast data from IRBank /results pages")
    p_fc.add_argument("--ticker", nargs="+", help="Specific ticker(s) to scrape")
    p_fc.add_argument("--force", action="store_true", help="Re-scrape even if data exists")
    p_fc.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")

    # fetch-shares
    p_shares = sub.add_parser("fetch-shares", parents=[_proxy_args], help="Fetch 発行済株式数 from kabutan into stocks.shares_outstanding")
    p_shares.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    p_shares.add_argument("--force", action="store_true", help="Re-fetch even if shares already cached")
    p_shares.add_argument("--workers", type=int, default=MAGIC["scrape"]["workers"], help="Number of parallel workers")

    # probe-proxies
    p_probe = sub.add_parser("probe-proxies", help="Probe public proxies without touching screening data")
    p_probe.add_argument("--proxy", help="Specific HTTP proxy URL to validate (e.g. http://host:port)")
    p_probe.add_argument("--target-proxies", type=int, default=CLI_DEFAULTS["probe_proxies"]["target_proxies"], help="Number of proxies to acquire")
    p_probe.add_argument("--check-sites", type=int, default=CLI_DEFAULTS["probe_proxies"]["check_sites"], help="Number of quality sites each proxy must pass")
    p_probe.add_argument("--clear-legacy-cache", action="store_true", help="Remove only legacy failure-cache entries before probing")

    # clear-failure-cache
    from formula_screening.stealth import failure_cache_reasons

    p_clear = sub.add_parser("clear-failure-cache", help="Clear proxy failure-cache entries by reason")
    p_clear.add_argument("--reason", action="append", choices=failure_cache_reasons(), help="Failure reason to remove; repeat to remove multiple reasons")
    p_clear.add_argument("--all", action="store_true", help="Remove all active failure-cache entries")

    # screen
    p_screen = sub.add_parser("screen", parents=[_proxy_args], help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")
    p_screen.add_argument("--open", nargs="?", type=int, const=0, default=None,
                           help="Open top N hits on Shikiho Online (omit N for all)")
    p_screen.add_argument("--workers", type=int, default=MAGIC["screening"]["workers"], help="Number of parallel screening workers")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    cmds = {
        "import-irbank": _cmd_import_irbank,
        "fetch-prices": _cmd_fetch_prices,
        "fetch-shares": _cmd_fetch_shares,
        "scrape-bs": _cmd_scrape_bs,
        "scrape-forecast": _cmd_scrape_forecast,
        "probe-proxies": _cmd_probe_proxies,
        "clear-failure-cache": _cmd_clear_failure_cache,
        "screen": _cmd_screen,
    }
    try:
        cmds[args.command](args)
    except ProxyUnavailableError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        sys.exit(1)
