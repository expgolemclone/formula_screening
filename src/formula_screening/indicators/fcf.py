"""FCF yield indicator."""

from __future__ import annotations

from formula_screening.config import MAGIC

_FCF_YEARS: int = MAGIC["screening"]["fcf_years"]


def _resolve_free_cf(cf: dict[str, float | None]) -> float | None:
    """Derive free CF from a single-period CF dict."""
    free_cf: float | None = cf.get("free_cf")
    if free_cf is not None:
        return free_cf
    operating_cf: float | None = cf.get("operating_cf")
    investing_cf: float | None = cf.get("investing_cf")
    if operating_cf is not None and investing_cf is not None:
        return operating_cf + investing_cf
    return None


def fcf_yield_avg(stock: dict, years: int = _FCF_YEARS) -> float | None:
    """Return the average FCF yield over *years* periods.

    Uses historical market cap (price at each period × shares outstanding)
    to avoid look-ahead bias.
    """
    shares: int | None = stock.get("shares_outstanding")
    if not shares:
        return None
    shares_f = float(shares)

    price_at_period: dict[str, float | None] = stock.get("price_at_period", {})

    cf_history: list[tuple[str, dict[str, float | None]]] = stock["cf_history"]
    if not cf_history:
        return None

    yields: list[float] = []
    for period, cf in cf_history[:years]:
        fcf: float | None = _resolve_free_cf(cf)
        period_price: float | None = price_at_period.get(period)
        if fcf is not None and period_price is not None and period_price > 0:
            market_cap = period_price * shares_f
            yields.append(fcf / market_cap)

    if not yields:
        return None
    return sum(yields) / len(yields)
