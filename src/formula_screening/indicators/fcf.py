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

    FCF yield = FCF / market_cap for each historical period.

    NOTE: Uses current market_cap for all periods — contains look-ahead bias.
    Not suitable for backtesting, but practical for live screening.
    """
    market_cap: float | None = stock["metrics"]["market_cap"]
    if not market_cap or market_cap <= 0:
        return None

    cf_history: list[tuple[str, dict[str, float | None]]] = stock["cf_history"]
    if not cf_history:
        return None

    yields: list[float] = []
    for _period, cf in cf_history[:years]:
        fcf: float | None = _resolve_free_cf(cf)
        if fcf is not None:
            yields.append(fcf / market_cap)

    if not yields:
        return None
    return sum(yields) / len(yields)
