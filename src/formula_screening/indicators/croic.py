"""CROIC (Cash Return on Invested Capital) indicator."""

from __future__ import annotations


def croic(stock: dict) -> float | None:
    """Return FCF / invested capital (stockholders_equity + interest_bearing_debt)."""
    metrics: dict[str, float | None] = stock["metrics"]

    free_cf: float | None = metrics["free_cf"]
    if free_cf is None:
        return None

    stockholders_equity: float | None = stock["bs"].get("stockholders_equity")
    if stockholders_equity is None:
        return None

    interest_bearing_debt: float | None = metrics["interest_bearing_debt"]
    if interest_bearing_debt is None:
        return None

    invested_capital: float = stockholders_equity + interest_bearing_debt
    if invested_capital <= 0:
        return None
    return free_cf / invested_capital
