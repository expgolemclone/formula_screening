"""Compute derived metrics from financial data + real-time price."""

from __future__ import annotations


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a: float | None, b: float | None) -> float | None:
    val = _safe_div(a, b)
    return val * 100 if val is not None else None


def _prefer(direct: float | None, fallback: float | None) -> float | None:
    """Return *direct* when present (including 0.0), otherwise *fallback*."""
    return direct if direct is not None else fallback


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
        price: Current stock price from Stooq.
        shares_outstanding: Current shares outstanding (from IR BANK or Stooq).

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
    forecast_net_income = financials.get("forecast", {}).get("net_income")

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

    # Price-dependent metrics (always computed from real-time data).
    # PER is market_cap / net_income rather than price / EPS: per-share
    # values in the IR BANK JSON aren't adjusted for stock splits, so the
    # total-value form is split-safe.
    metrics["market_cap"] = market_cap
    metrics["per"] = _safe_div(market_cap, forecast_net_income)
    metrics["per_actual"] = _safe_div(market_cap, net_income)
    metrics["pbr"] = _safe_div(market_cap, total_equity)

    dps = financials.get("dividend", {}).get("dps")
    metrics["dividend_yield"] = _pct(dps, price)

    # Margin metrics: prefer IR BANK direct values, compute as fallback
    metrics["gross_margin"] = _pct(gross_profit, revenue)
    metrics["operating_margin"] = _prefer(pl.get("operating_margin"), _pct(operating_income, revenue))
    metrics["ordinary_margin"] = _prefer(pl.get("ordinary_income_margin"), _pct(ordinary_income, revenue))
    metrics["net_income_margin"] = _prefer(pl.get("net_income_margin"), _pct(net_income, revenue))

    # Return metrics: prefer IR BANK direct values
    metrics["roe"] = _prefer(pl.get("roe"), _pct(net_income, stockholders_equity))
    metrics["roa"] = _prefer(pl.get("roa"), _pct(net_income, total_assets))

    # Balance sheet ratios
    metrics["equity_ratio"] = _prefer(bs.get("equity_ratio"), _pct(stockholders_equity, total_assets))
    metrics["debt_equity_ratio"] = _prefer(bs.get("debt_equity_ratio"), _pct(total_debt, stockholders_equity))

    # Cash flow ratios
    metrics["operating_cf_margin"] = _prefer(cf.get("operating_cf_margin"), _pct(cf.get("operating_cf"), revenue))
    metrics["free_cf_ratio"] = _pct(free_cf, revenue)

    # Total liabilities
    total_liabilities = None
    if total_assets is not None and total_equity is not None:
        total_liabilities = total_assets - total_equity
    metrics["total_liabilities"] = total_liabilities

    short_term_debt = bs.get("short_term_debt")
    long_term_debt = bs.get("long_term_debt")
    interest_bearing_debt = None
    if short_term_debt is not None or long_term_debt is not None:
        interest_bearing_debt = (short_term_debt or 0) + (long_term_debt or 0)
    metrics["interest_bearing_debt"] = interest_bearing_debt

    # Net cash (清原達郎)
    # Full formula: 流動資産 − 棚卸資産 + 投資有価証券×70% − 負債
    # Fallback:     現金同等物 − 負債 (when detailed BS unavailable)
    current_assets = bs.get("current_assets")
    inventories = bs.get("inventories")
    investment_securities = bs.get("investment_securities")
    current_liabilities = bs.get("current_liabilities")
    non_current_liabilities = bs.get("non_current_liabilities") or bs.get("non_current_liabilities_total")

    net_cash = None
    if current_assets is not None and (current_liabilities is not None or non_current_liabilities is not None):
        liabilities = (current_liabilities or 0) + (non_current_liabilities or 0)
        net_cash = current_assets - (inventories or 0) + (investment_securities or 0) * 0.7 - liabilities
    elif cf.get("cash_equivalents") is not None and total_liabilities is not None:
        net_cash = cf["cash_equivalents"] - total_liabilities
    metrics["net_cash"] = net_cash
    metrics["net_cash_ratio"] = _safe_div(net_cash, market_cap)

    return metrics
