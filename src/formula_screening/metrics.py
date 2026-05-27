"""Compute derived metrics from financial data + real-time price."""

from __future__ import annotations


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a: float | None, b: float | None) -> float | None:
    val = _safe_div(a, b)
    return val * 100 if val is not None else None


def _total_payout_return_ratio(
    dividend_payment: float | None,
    treasury_stock_purchase: float | None,
    market_cap: float | None,
) -> float | None:
    if market_cap is None or market_cap <= 0:
        return None

    payout_total = 0.0
    has_payout = False
    for value in (dividend_payment, treasury_stock_purchase):
        if value is None:
            continue
        payout_total += abs(value)
        has_payout = True
    if not has_payout:
        return None
    return payout_total / market_cap * 100


def compute_metrics(
    financials: dict[str, dict[str, float | None]],
    price: float | None,
    shares_outstanding: int | None,
) -> dict[str, float | None]:
    """Compute derived screening metrics from raw financial data.

    Args:
        financials: Nested dict {statement: {item_name: value}} from DB.
        price: Latest stock price from stock_db price data.
        shares_outstanding: Current shares outstanding from stock_db.stocks.

    Returns:
        Dict of metric_name -> value.
    """
    pl = financials["pl"]
    bs = financials["bs"]
    cf = financials["cf"]

    shares = float(shares_outstanding) if shares_outstanding else None
    market_cap = (price * shares) if price and shares else None

    revenue = pl.get("revenue")
    operating_income = pl.get("operating_income")
    ordinary_income = pl.get("ordinary_income")
    net_income = pl.get("net_income")
    ni_current = financials.get("forecast", {}).get("net_income_current")
    ni_next = financials.get("forecast", {}).get("net_income_next")

    total_assets = bs.get("total_assets")
    stockholders_equity = bs.get("stockholders_equity")
    total_equity = bs.get("total_equity")
    total_debt = bs.get("total_debt")

    gross_profit = None
    cost_of_revenue = pl.get("cost_of_revenue")
    if revenue is not None and cost_of_revenue is not None:
        gross_profit = revenue - cost_of_revenue

    free_cf: float | None = cf.get("free_cf")
    if free_cf is None:
        operating_cf: float | None = cf.get("operating_cf")
        investing_cf: float | None = cf.get("investing_cf")
        if operating_cf is not None and investing_cf is not None:
            free_cf = operating_cf + investing_cf

    metrics: dict[str, float | None] = {}

    # Price-dependent metrics use market_cap / net_income rather than
    # price / EPS, keeping calculations independent from per-share adjustment
    # differences across data sources.
    metrics["market_cap"] = market_cap
    metrics["per"] = _safe_div(market_cap, ni_current)
    metrics["per_next"] = _safe_div(market_cap, ni_next)
    metrics["per_actual"] = _safe_div(market_cap, net_income)
    metrics["pbr"] = _safe_div(market_cap, total_equity)

    dps = financials.get("dividend", {}).get("dps")
    metrics["dividend_yield"] = _pct(dps, price)
    metrics["tprr"] = _total_payout_return_ratio(
        financials.get("dividend", {}).get("dividend_payment"),
        cf.get("treasury_stock_purchase"),
        market_cap,
    )

    metrics["gross_margin"] = _pct(gross_profit, revenue)
    metrics["operating_margin"] = _pct(operating_income, revenue)
    metrics["ordinary_margin"] = _pct(ordinary_income, revenue)
    metrics["net_income_margin"] = _pct(net_income, revenue)

    metrics["roe"] = _pct(net_income, stockholders_equity)
    metrics["roa"] = _pct(net_income, total_assets)

    metrics["equity_ratio"] = _pct(stockholders_equity, total_assets)
    metrics["debt_equity_ratio"] = _pct(total_debt, stockholders_equity)

    metrics["operating_cf_margin"] = _pct(cf.get("operating_cf"), revenue)
    metrics["free_cf"] = free_cf
    metrics["free_cf_ratio"] = _pct(free_cf, revenue)

    # Total liabilities
    total_liabilities = None
    if total_assets is not None and total_equity is not None:
        total_liabilities = total_assets - total_equity
    metrics["total_liabilities"] = total_liabilities

    short_term_debt = bs.get("short_term_debt")
    long_term_debt = bs.get("long_term_debt")
    metrics["interest_bearing_debt"] = (short_term_debt or 0) + (long_term_debt or 0)

    # Net cash (清原達郎): 流動資産 − 棚卸資産 + 投資有価証券×70% − 負債
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
        liabilities: float = current_liabilities + non_current_liabilities
        net_cash = current_assets - liabilities
        if inventories is not None:
            net_cash -= inventories
        if investment_securities is not None:
            net_cash += investment_securities * 0.7
    metrics["net_cash"] = net_cash
    metrics["net_cash_ratio"] = _safe_div(net_cash, market_cap)

    return metrics
