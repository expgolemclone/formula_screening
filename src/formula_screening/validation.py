from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from stock_db.storage.financials import get_items_by_source
from stock_db.storage.stocks import get_validation_targets as _stock_db_get_validation_targets

from formula_screening.net_cash import compute_net_cash_metrics


@dataclass(frozen=True)
class ValidationTarget:
    ticker: str
    name: str
    securities_report_url: str
    price: float
    shares_outstanding: int


@dataclass(frozen=True)
class NetCashSnapshot:
    period: str
    market_cap: float | None
    net_cash: float | None
    net_cash_ratio: float | None


def select_validation_targets(
    conn: sqlite3.Connection,
    limit: int,
) -> list[ValidationTarget]:
    rows = _stock_db_get_validation_targets(conn, limit)
    return [
        ValidationTarget(
            ticker=row["ticker"],
            name=row["name"],
            securities_report_url=row["securities_report_url"],
            price=float(row["close"]),
            shares_outstanding=int(row["shares_outstanding"]),
        )
        for row in rows
    ]


def load_latest_bs(
    conn: sqlite3.Connection,
    ticker: str,
) -> tuple[str | None, dict[str, float | None], str | None]:
    """Load the latest balance sheet rows stored from EDINET XBRL."""
    rows = get_items_by_source(conn, ticker, "edinet_xbrl")
    if not rows:
        return None, {}, "scrape_missing"

    status_rows = [row for row in rows if row["statement"] == "_status"]
    data_rows = [row for row in rows if row["statement"] == "bs"]
    if data_rows:
        latest_period = max(row["period"] for row in data_rows)
        bs = {
            row["item_name"]: row["value"]
            for row in data_rows
            if row["period"] == latest_period
        }
        return latest_period, bs, None
    if status_rows:
        return None, {}, f"scrape_{status_rows[0]['item_name']}"
    return None, {}, "scrape_missing"


def build_net_cash_snapshot(
    period: str,
    bs: dict[str, float | None],
    price: float | None,
    shares_outstanding: int | None,
) -> NetCashSnapshot:
    metrics = compute_net_cash_metrics(bs, price, shares_outstanding)
    return NetCashSnapshot(
        period=period,
        market_cap=metrics["market_cap"],
        net_cash=metrics["net_cash"],
        net_cash_ratio=metrics["net_cash_ratio"],
    )
