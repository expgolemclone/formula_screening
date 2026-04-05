"""CROIC (Cash Return on Invested Capital) indicator."""

from __future__ import annotations


def croic(stock: dict) -> float | None:
    """Return FCF / invested capital (stockholders_equity + interest_bearing_debt)."""
    cf: dict[str, float | None] = stock.get("cf", {})
    bs: dict[str, float | None] = stock.get("bs", {})

    free_cf: float | None = cf.get("free_cf")
    if free_cf is None:
        operating_cf: float | None = cf.get("operating_cf")
        investing_cf: float | None = cf.get("investing_cf")
        if operating_cf is not None and investing_cf is not None:
            free_cf = operating_cf + investing_cf

    if free_cf is None:
        return None

    stockholders_equity: float | None = bs.get("stockholders_equity")
    if stockholders_equity is None:
        return None

    short_term_debt: float | None = bs.get("short_term_debt")
    long_term_debt: float | None = bs.get("long_term_debt")
    interest_bearing_debt: float = (short_term_debt or 0) + (long_term_debt or 0)
    invested_capital: float = stockholders_equity + interest_bearing_debt

    if invested_capital <= 0:
        return None
    return free_cf / invested_capital
