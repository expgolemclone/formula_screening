"""Compute derived metrics from financial data + real-time price."""

from __future__ import annotations


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a: float | None, b: float | None) -> float | None:
    val = _safe_div(a, b)
    return val * 100 if val is not None else None


def compute_metrics(
    financials: dict[str, dict[str, float | None]],
    price: float | None,
    shares_outstanding: int | None,
) -> dict[str, float | None]:
    """Compute derived screening metrics.

    Uses IR BANK values when available (ROE, operating_margin, etc.),
    falling back to calculation from raw data.

    Args:
        financials: Nested dict {statement: {item_name: value}} from DB.
        price: Current stock price from yfinance.
        shares_outstanding: Current shares outstanding from yfinance.

    Returns:
        Dict of metric_name -> value.
    """
    pl = financials.get("pl", {})
    bs = financials.get("bs", {})
    cf = financials.get("cf", {})

    shares = float(shares_outstanding) if shares_outstanding else None
    market_cap = (price * shares) if price and shares else None

    revenue = pl.get("revenue")
    operating_income = pl.get("operating_income")
    ordinary_income = pl.get("ordinary_income")
    net_income = pl.get("net_income")
    basic_eps = pl.get("basic_eps")

    total_assets = bs.get("total_assets")
    stockholders_equity = bs.get("stockholders_equity")
    total_equity = bs.get("total_equity")
    total_debt = bs.get("total_debt")

    gross_profit = None
    cost_of_revenue = pl.get("cost_of_revenue")
    if revenue is not None and cost_of_revenue is not None:
        gross_profit = revenue - cost_of_revenue

    free_cf = cf.get("free_cf")

    metrics: dict[str, float | None] = {}

    # Price-dependent metrics (always computed from real-time data)
    metrics["market_cap"] = market_cap
    metrics["per"] = _safe_div(price, basic_eps)
    metrics["pbr"] = _safe_div(market_cap, total_equity)

    dps = financials.get("dividend", {}).get("dps")
    metrics["dividend_yield"] = _pct(dps, price)

    # Margin metrics: prefer IR BANK direct values, compute as fallback
    metrics["gross_margin"] = _pct(gross_profit, revenue)
    metrics["operating_margin"] = pl.get("operating_margin") or _pct(operating_income, revenue)
    metrics["ordinary_margin"] = pl.get("ordinary_income_margin") or _pct(ordinary_income, revenue)
    metrics["net_income_margin"] = pl.get("net_income_margin") or _pct(net_income, revenue)

    # Return metrics: prefer IR BANK direct values
    metrics["roe"] = pl.get("roe") or _pct(net_income, stockholders_equity)
    metrics["roa"] = pl.get("roa") or _pct(net_income, total_assets)

    # Balance sheet ratios
    metrics["equity_ratio"] = bs.get("equity_ratio") or _pct(stockholders_equity, total_assets)
    metrics["debt_equity_ratio"] = bs.get("debt_equity_ratio") or _pct(total_debt, stockholders_equity)

    # Cash flow ratios
    metrics["operating_cf_margin"] = cf.get("operating_cf_margin") or _pct(cf.get("operating_cf"), revenue)
    metrics["free_cf_ratio"] = _pct(free_cf, revenue)

    return metrics
