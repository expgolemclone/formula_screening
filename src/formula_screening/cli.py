"""CLI entry point for the screening tool."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path

from formula_screening.db.schema import get_connection, init_db
from formula_screening.log import setup_logging


def _cmd_fetch_list(args: argparse.Namespace) -> None:
    from formula_screening.datasources.stocklist import (
        fetch_edinetdb_companies,
        load_manual_stocklist,
    )

    conn = get_connection()
    try:
        if args.file:
            count = load_manual_stocklist(conn, Path(args.file))
        else:
            count = fetch_edinetdb_companies(conn)
        print(f"{count} stocks registered.")
    finally:
        conn.close()


def _resolve_tickers(conn: sqlite3.Connection, args: argparse.Namespace) -> set[str] | None:
    """Resolve ticker filter from CLI args."""
    from formula_screening.db.repository import get_all_tickers

    if args.ticker:
        return set(args.ticker)
    if not args.all:
        return set(get_all_tickers(conn))
    return None


def _cmd_fetch_data(args: argparse.Namespace) -> None:
    from formula_screening.datasources.edinetdb import fetch_all_financials

    conn = get_connection()
    try:
        tickers = _resolve_tickers(conn, args)
        total = fetch_all_financials(conn, tickers=tickers, years=args.years)
        print(f"{total} financial items saved.")
    finally:
        conn.close()


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

    # fetch-list
    p_list = sub.add_parser("fetch-list", help="Download/update stock list from EDINET DB")
    p_list.add_argument("--file", help="Path to a manual ticker list (one per line)")

    # fetch-data
    p_data = sub.add_parser("fetch-data", help="Download financial data from EDINET DB")
    p_data.add_argument("--ticker", nargs="+", help="Specific ticker(s) to fetch")
    p_data.add_argument("--all", action="store_true", help="Fetch all tickers (ignore DB stock list)")
    p_data.add_argument("--years", type=int, default=6, help="Number of fiscal years to fetch (default: 6, max: 6)")

    # screen
    p_screen = sub.add_parser("screen", help="Run a screening strategy")
    p_screen.add_argument("--strategy", "-s", required=True, help="Path to strategy .py file")
    p_screen.add_argument("--output", "-o", help="Write results to CSV file")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    init_db()

    cmds = {
        "fetch-list": _cmd_fetch_list,
        "fetch-data": _cmd_fetch_data,
        "screen": _cmd_screen,
    }
    cmds[args.command](args)
