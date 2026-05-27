#!/usr/bin/env python
"""Diagnose whether FCF_10Y% has enough valid cash-flow periods."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from pathlib import Path

from formula_screening.config import MAGIC
from formula_screening.fcf_history_diagnostics import (
    diagnose_records,
    format_summary,
    write_diagnostics_csv,
)
import formula_screening.stock_db_compat as stock_db_api


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    required_periods = args.years
    tickers = args.tickers or stock_db_api.get_screening_tickers()
    records = _load_records(tickers, required_periods, args.chunk_size)
    summary = diagnose_records(records, required_periods=required_periods)

    print(format_summary(summary, sample_count=args.samples))
    if args.csv is not None:
        write_diagnostics_csv(args.csv, summary.diagnostics)
        print(f"csv={args.csv}")
    return 0


def _load_records(
    tickers: Sequence[str],
    required_periods: int,
    chunk_size: int,
) -> Iterable[dict]:
    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start : start + chunk_size]
        yield from stock_db_api.load_screening_stocks(
            chunk,
            fcf_periods=required_periods,
            pl_periods=1,
        )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify stocks whose FCF yield cannot use the required number "
            "of valid CF periods."
        )
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Optional ticker list. Defaults to formula_screening.stock_db_compat.get_screening_tickers().",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=MAGIC["screening"]["fcf_years"],
        help="Required valid FCF periods.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Number of tickers to load per stock_db API call.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=8,
        help="Sample rows to print for each under-coverage cause.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Optional CSV path for all under-coverage rows.",
    )
    args = parser.parse_args(argv)
    if args.years < 1:
        parser.error("--years must be >= 1")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")
    if args.samples < 0:
        parser.error("--samples must be >= 0")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
