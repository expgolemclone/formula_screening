from __future__ import annotations

from dataclasses import dataclass

import stock_db.api as stock_db_api

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
    conn: object | None,
    limit: int,
) -> list[ValidationTarget]:
    _reject_connection(conn)
    rows = stock_db_api.get_validation_targets(limit)
    return [
        ValidationTarget(
            ticker=row["ticker"],
            name=row["name"],
            securities_report_url=row["securities_report_url"],
            price=float(row["price"]),
            shares_outstanding=int(row["shares_outstanding"]),
        )
        for row in rows
    ]


def load_latest_bs(
    conn: object | None,
    ticker: str,
) -> tuple[str | None, dict[str, float | None], str | None]:
    """Load the latest balance sheet rows stored from EDINET XBRL."""
    _reject_connection(conn)
    return stock_db_api.get_latest_balance_sheet(ticker)


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


def _reject_connection(conn: object | None) -> None:
    if conn is not None:
        msg = "formula_screening.validation no longer accepts sqlite connections"
        raise TypeError(msg)
