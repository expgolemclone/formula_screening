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

from rich.console import Console
from rich.table import Table
from rich.text import Text
import rich.box

from formula_screening.config import CLI_DEFAULTS, MAGIC
from formula_screening.db.schema import get_connection, init_db
from formula_screening.log import setup_logging
from formula_screening.screen_output import LinkCell

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
        from formula_screening.db.repository import get_all_tickers
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
        from formula_screening.db.repository import get_all_tickers
        lo, hi = int(m.group(1)), int(m.group(2))
        all_tickers = get_all_tickers(conn)
        return [t for t in all_tickers if t.isdigit() and lo <= int(t) <= hi]

    # bare value → single ticker
    return [spec]


def _cmd_screen(args: argparse.Namespace) -> None:
    from formula_screening.screener import load_strategy, run_screening

    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    strategy_mod = load_strategy(strategy_path)
    extra_cols_fn: _ExtraColsFn | None = getattr(strategy_mod, "columns", None)

    conn: sqlite3.Connection = get_connection()
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
                # One or more bare ticker codes passed directly
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

        sort_key_fn = getattr(strategy_mod, "sort_key", None)
        if sort_key_fn is not None:
            stocks.sort(key=sort_key_fn, reverse=True)
        else:
            stocks.sort(key=lambda s: s["metrics"].get("net_cash_ratio") or 0, reverse=True)

        _print_table(stocks, extra_cols_fn=extra_cols_fn)
        print(f"\n{len(stocks)} stocks shown ({elapsed:.1f}s)")

        if args.output:
            _write_csv(stocks, Path(args.output), extra_cols_fn=extra_cols_fn)
            print(f"Results written to {args.output}")

        if args.open is not None:
            to_open: list[dict] = stocks[:args.open] if args.open > 0 else stocks
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


def _cell(text: str, *, good: bool | None = None) -> str | Text:
    """Return a styled Text cell or plain string."""
    if text == "-":
        return Text(text, style="dim")
    if good is True:
        return Text(text, style="green")
    if good is False:
        return Text(text, style="red")
    return text


def _style_signed(text: str) -> str | Text:
    """Color a formatted numeric string green or red by sign."""
    if text == "-":
        return Text(text, style="dim")
    return Text(text, style="green" if not text.startswith("-") else "red")


def _print_table(
    hits: list[dict],
    *,
    extra_cols_fn: _ExtraColsFn | None = None,
) -> None:
    """Print screening results as a rich table to stdout."""
    console = Console(width=200)
    table = Table(box=rich.box.HEAVY_HEAD, show_lines=False, expand=False,
                  header_style="bold cyan")
    table.add_column("Ticker")
    table.add_column("Name", max_width=20, no_wrap=True)
    table.add_column("Price", justify="right")
    table.add_column("NC_Ratio", justify="right")
    table.add_column("PER", justify="right")
    table.add_column("PBR", justify="right")
    table.add_column("Div%", justify="right")
    extra_headers_added = False

    for s in hits:
        m = s["metrics"]
        pbr_val = m.get("pbr")
        div_val = m.get("dividend_yield")
        row: list[str | Text] = [
            s["ticker"],
            s["name"] or "",
            _cell(f'{s["price"]:.0f}' if s["price"] else "-"),
            _cell(
                f'{m["net_cash_ratio"]:.2f}' if m.get("net_cash_ratio") else "-",
                good=m.get("net_cash_ratio") is not None,
            ),
            _cell(
                f'{m["per"]:.1f}' if m.get("per") else "-",
                good=m.get("per") is not None,
            ),
            _cell(
                f'{pbr_val:.2f}' if pbr_val is not None else "-",
                good=pbr_val is not None and pbr_val < 1.0,
            ),
            _cell(
                f'{div_val:.2f}' if div_val is not None else "-",
                good=div_val is not None and div_val > 0,
            ),
        ]
        if extra_cols_fn is not None:
            extra = extra_cols_fn(s)
            if not extra_headers_added:
                for h, _ in extra:
                    table.add_column(h, justify="right")
                extra_headers_added = True
            for _, v in extra:
                if isinstance(v, LinkCell):
                    row.append(Text(v.label, style=f"link {v.url}"))
                else:
                    row.append(_style_signed(str(v)))
        table.add_row(*row)

    console.print(table)


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
                    row[header] = value
            writer.writerow(row)


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
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")
    p_screen.add_argument("--open", nargs="?", type=int, const=0, default=None,
                           help="Open top N hits on Shikiho Online (omit N for all)")
    p_screen.add_argument(
        "--ticker", "-t", type=str, nargs="+", default=None,
        help="Ticker(s) to screen: codes (7203 6758), 'all', a range (1000-2000), or csv:path.csv",
    )
    p_screen.add_argument("--show-all", action="store_true", help="Show all screened stocks, not just hits")
    p_screen.add_argument("--workers", type=int, default=MAGIC["screening"]["workers"], help="Number of parallel screening workers")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    cmds = {
        "screen": _cmd_screen,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
