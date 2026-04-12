#!/usr/bin/env python3
"""Export all financial data + metrics to CSV for all tickers.

Usage:
    uv run python scripts/export_csv.py [-o OUTPUT]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.config import CLI_DEFAULTS, PATHS
from formula_screening.db.schema import get_connection, init_db
from formula_screening.screener import build_stock_dict

# Flat CSV columns: raw financials + price + computed metrics
_FIELDNAMES = [
    "ticker", "name", "period",
    # BS
    "total_assets", "total_equity", "stockholders_equity",
    "retained_earnings", "short_term_debt", "long_term_debt",
    "bps", "equity_ratio",
    # PL
    "revenue", "operating_income", "ordinary_income", "net_income",
    "basic_eps", "roe", "roa",
    # CF
    "operating_cf", "investing_cf", "financing_cf",
    "capex", "cash_equivalents", "operating_cf_margin",
    # Dividend
    "dps", "dividend_payment", "buyback",
    "payout_ratio", "total_return_ratio", "doe",
    # Price
    "price", "shares_outstanding",
    # Computed metrics
    "market_cap", "per", "pbr", "dividend_yield",
    "total_liabilities", "interest_bearing_debt",
    "net_cash", "net_cash_ratio",
]


def _flatten(stock: dict, period: str) -> dict:
    """Flatten nested stock dict into a single-level row for CSV."""
    row: dict = {
        "ticker": stock["ticker"],
        "name": stock["name"],
        "period": period,
        "price": stock["price"],
        "shares_outstanding": stock["shares_outstanding"],
    }
    for statement in ("bs", "pl", "cf", "dividend"):
        row.update(stock[statement])
    row.update(stock["metrics"])
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Export all data to CSV")
    parser.add_argument("-o", "--output", default=CLI_DEFAULTS["export_csv"]["output"])
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    stocks = conn.execute("SELECT ticker, name FROM stocks ORDER BY ticker").fetchall()
    print(f"{len(stocks)} tickers", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()

        for i, stock_row in enumerate(stocks, 1):
            ticker = stock_row["ticker"]
            name = stock_row["name"]

            # 最新period取得
            row = conn.execute(
                "SELECT period FROM financial_items "
                "WHERE ticker = ? AND statement = 'pl' "
                "ORDER BY period DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            if row is None:
                continue

            stock = build_stock_dict(conn, ticker, name)
            writer.writerow(_flatten(stock, row["period"]))
            written += 1

            if i % 500 == 0:
                print(f"  {i}/{len(stocks)}...", file=sys.stderr)

    conn.close()
    print(f"{written} rows written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
