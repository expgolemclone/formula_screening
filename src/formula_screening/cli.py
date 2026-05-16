"""CLI entry point for the screening tool."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

from collections.abc import Callable

from formula_screening.config import CLI_DEFAULTS, MAGIC
from formula_screening.log import setup_logging
from formula_screening.price_updates import ensure_stooq_prices_fresh
from stock_db.paths import STOCKS_DB_PATH
from stock_db.sources.stooq import StooqDailyPriceUpdateError
from stock_db.storage.connection import get_connection

_GH_PAGES_JSON = Path(__file__).resolve().parent.parent.parent / "docs" / "assets" / "screening.json"

_ExtraColsFn = Callable[[dict], list[tuple[str, str]]]
logger = logging.getLogger("formula_screening.cli")

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _parse_ticker_spec(spec: str, conn: sqlite3.Connection) -> list[str]:
    """Resolve ``--ticker`` value into a concrete list of ticker strings.

    Supported formats::

        7203          → single ticker
        all           → every ticker in the DB
        1000-2000     → DB tickers whose numeric code falls in [1000, 2000]
        csv:path.csv  → tickers read from the first column of *path.csv*
    """
    if spec == "all":
        from stock_db.storage.stocks import get_all_tickers
        return get_all_tickers(conn)

    if spec.startswith("csv:"):
        csv_path = Path(spec[4:])
        if not csv_path.exists():
            print(f"CSV file not found: {csv_path}", file=sys.stderr)
            sys.exit(1)
        tickers: list[str] = []
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row:
                    val = row[0].strip()
                    if val:
                        tickers.append(val)
        if not tickers:
            print(f"No tickers found in {csv_path}", file=sys.stderr)
            sys.exit(1)
        return tickers

    m = _RANGE_RE.match(spec)
    if m:
        from stock_db.storage.stocks import get_all_tickers
        lo, hi = int(m.group(1)), int(m.group(2))
        all_tickers = get_all_tickers(conn)
        return [t for t in all_tickers if t.isdigit() and lo <= int(t) <= hi]

    # bare value → single ticker
    return [spec]


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening.screener import load_strategy, run_screening
    from formula_screening.web import serve_screening

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    try:
        update_result = ensure_stooq_prices_fresh(db_path=STOCKS_DB_PATH)
    except (StooqDailyPriceUpdateError, ValueError) as exc:
        print(f"Failed to update Stooq prices: {exc}", file=sys.stderr)
        sys.exit(1)

    if update_result is not None:
        update_message = (update_result.stderr or update_result.stdout).strip()
        suffix = f": {update_message}" if update_message else ""
        print(
            f"Updated Stooq prices{suffix}",
            file=sys.stderr,
        )

    conn: sqlite3.Connection = get_connection(STOCKS_DB_PATH)
    try:
        # Determine which tickers to screen
        specific_tickers: list[str] | None = None
        if args.ticker:
            if len(args.ticker) == 1 and (
                args.ticker[0] == "all"
                or _RANGE_RE.match(args.ticker[0])
                or args.ticker[0].startswith("csv:")
            ):
                specific_tickers = _parse_ticker_spec(args.ticker[0], conn)
            else:
                specific_tickers = args.ticker

        start: float = time.monotonic()
        stocks: list[dict] = run_screening(
            conn, strategy_path, workers=args.workers, tickers=specific_tickers,
            return_all=args.show_all,
        )
        elapsed: float = time.monotonic() - start

        if not stocks:
            print("No stocks matched the screening criteria.")
            return

        sort_key_fn = getattr(load_strategy(strategy_path), "sort_key", None)
        if sort_key_fn is not None:
            stocks.sort(key=sort_key_fn, reverse=True)
        else:
            stocks.sort(key=lambda s: s["metrics"].get("net_cash_ratio") or 0, reverse=True)

        print(f"{len(stocks)} stocks matched ({elapsed:.1f}s)", flush=True)

        from formula_screening.web import save_screening_json
        save_screening_json(stocks, _GH_PAGES_JSON)

        if args.json:
            save_screening_json(stocks, Path(args.json))
            print(f"Saved to {args.json}")
            return

        serve_screening(stocks)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="formula_screening",
        description="Screen Japanese stocks with user-defined Python formulas.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    # screen
    p_screen = sub.add_parser("screen", help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument(
        "--ticker", "-t", type=str, nargs="+", default=None,
        help="Ticker(s) to screen: codes (7203 6758), 'all', a range (1000-2000), or csv:path.csv",
    )
    p_screen.add_argument("--show-all", action="store_true", help="Show all screened stocks, not just hits")
    p_screen.add_argument("--json", type=str, default=None, metavar="PATH", help="Save results as JSON and exit (no web server)")
    p_screen.add_argument("--workers", type=int, default=MAGIC["screening"]["workers"], help="Number of parallel screening workers")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)

    cmds = {
        "screen": _cmd_screen,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
