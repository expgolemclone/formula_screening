"""PEG ratio indicator based on 5 periods of net income history."""

from __future__ import annotations

PEG_YEARS: int = 5


def peg_5(stock: dict) -> float | None:
    """Return actual-PER divided by 5-period net income CAGR percentage."""
    metrics: dict[str, float | None] = stock.get("metrics", {})
    per_actual: float | None = metrics.get("per_actual")
    if per_actual is None or per_actual <= 0:
        return None

    pl_history: list[tuple[str, dict[str, float | None]]] = stock.get("pl_history", [])
    if len(pl_history) < PEG_YEARS:
        return None

    net_incomes: list[float] = []
    for _period, pl in pl_history[:PEG_YEARS]:
        net_income: float | None = pl.get("net_income")
        if net_income is None or net_income <= 0:
            return None
        net_incomes.append(net_income)

    latest: float = net_incomes[0]
    oldest: float = net_incomes[-1]
    periods: int = len(net_incomes) - 1
    if periods <= 0:
        return None

    cagr: float = (latest / oldest) ** (1 / periods) - 1
    if cagr <= 0:
        return None

    return per_actual / (cagr * 100)
