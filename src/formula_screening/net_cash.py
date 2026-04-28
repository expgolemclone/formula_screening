from __future__ import annotations

from collections.abc import Mapping


def compute_net_cash_metrics(
    bs: Mapping[str, float | None],
    price: float | None,
    shares_outstanding: int | None,
) -> dict[str, float | None]:
    shares = float(shares_outstanding) if shares_outstanding else None
    market_cap = (price * shares) if price and shares else None

    current_assets = bs.get("current_assets")
    inventories = bs.get("inventories")
    investment_securities = bs.get("investment_securities")
    current_liabilities = bs.get("current_liabilities")
    non_current_liabilities = bs.get("non_current_liabilities")

    net_cash: float | None = None
    if (
        current_assets is not None
        and current_liabilities is not None
        and non_current_liabilities is not None
    ):
        liabilities = current_liabilities + non_current_liabilities
        net_cash = current_assets - liabilities
        if inventories is not None:
            net_cash -= inventories
        if investment_securities is not None:
            net_cash += investment_securities * 0.7

    net_cash_ratio: float | None = None
    if net_cash is not None and market_cap not in (None, 0):
        net_cash_ratio = net_cash / market_cap

    return {
        "market_cap": market_cap,
        "net_cash": net_cash,
        "net_cash_ratio": net_cash_ratio,
    }
