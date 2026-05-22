"""CLI entry point for the screening tool."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from pathlib import Path

from collections.abc import Callable

from formula_screening.config import CLI_DEFAULTS, MAGIC
from formula_screening.log import setup_logging
from formula_screening.price_updates import ensure_prices_fresh
from stock_db.api import get_all_tickers
from stock_db.sources.price_refresh import PriceRefreshError

_GH_PAGES_JSON = Path(__file__).resolve().parent.parent.parent / "docs" / "assets" / "screening.json"
_GH_PAGES_METADATA_JSON = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "assets" / "stock-price-meta.json"
)

_ExtraColsFn = Callable[[dict], list[tuple[str, str]]]
logger = logging.getLogger("formula_screening.cli")

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _parse_ticker_spec(spec: str) -> list[str]:
    """Resolve ``--ticker`` value into a concrete list of ticker strings.

    Supported formats::

        7203          → single ticker
        all           → every ticker in the DB
        1000-2000     → DB tickers whose numeric code falls in [1000, 2000]
        csv:path.csv  → tickers read from the first column of *path.csv*
    """
    if spec == "all":
        return get_all_tickers()

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
        lo, hi = int(m.group(1)), int(m.group(2))
        all_tickers = get_all_tickers()
        return [t for t in all_tickers if t.isdigit() and lo <= int(t) <= hi]

    # bare value → single ticker
    return [spec]


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening._core import run_screening_payload_with_diagnostics_py
    from formula_screening.web import (
        save_screening_payload_json,
        save_stock_price_metadata_json,
        serve_screening_payload,
    )

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    try:
        update_result = ensure_prices_fresh()
    except (PriceRefreshError, ValueError) as exc:
        print(f"Failed to update stock prices: {exc}", file=sys.stderr)
        sys.exit(1)

    if update_result is not None:
        update_message = (update_result.stderr or update_result.stdout).strip()
        suffix = f": {update_message}" if update_message else ""
        print(
            f"Updated stock prices{suffix}",
            file=sys.stderr,
        )

    specific_tickers: list[str] | None = None
    if args.ticker:
        if len(args.ticker) == 1 and (
            args.ticker[0] == "all"
            or _RANGE_RE.match(args.ticker[0])
            or args.ticker[0].startswith("csv:")
        ):
            specific_tickers = _parse_ticker_spec(args.ticker[0])
        else:
            specific_tickers = args.ticker

    start: float = time.monotonic()
    result: dict[str, list[dict]] = run_screening_payload_with_diagnostics_py(
        str(strategy_path),
        specific_tickers,
        args.show_all,
    )
    payload: list[dict] = result["payload"]
    for diagnostic in result["diagnostics"]:
        logger.error(
            "Missing screening fields for %s (%s): %s",
            diagnostic["code"],
            diagnostic["name"],
            ", ".join(diagnostic["missing_fields"]),
        )
    elapsed: float = time.monotonic() - start

    if not payload:
        print("No stocks matched the screening criteria.")
        return

    print(f"{len(payload)} stocks matched ({elapsed:.1f}s)", flush=True)
    save_screening_payload_json(payload, _GH_PAGES_JSON)
    save_stock_price_metadata_json(_GH_PAGES_METADATA_JSON)

    if args.json:
        save_screening_payload_json(payload, Path(args.json))
        print(f"Saved to {args.json}")
        return

    serve_screening_payload(payload)


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
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .toml file")
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
