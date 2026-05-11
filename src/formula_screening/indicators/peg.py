"""PEG ratio indicators: trailing PEG and blended 2-forecast PEG.

Both functions use EPS (not net_income) for the standard PEG definition.
EPS data comes from stock_db's ``compute_eps`` CLI, stored as
``pl.item_name='eps'``, ``forecast.item_name='eps_current'/'eps_next'``.
"""

from __future__ import annotations


def peg_trailing(stock: dict, years: int) -> float | None:
    """Return Trailing PEG: ``per_actual / EPS CAGR[%]`` over *years* periods.

    Uses ``pl_history[:years + 1]`` EPS values so that a 5-year CAGR needs
    6 data points.
    """
    metrics: dict[str, float | None] = stock.get("metrics", {})
    per_actual: float | None = metrics.get("per_actual")
    if per_actual is None or per_actual <= 0:
        return None

    pl_history: list[tuple[str, dict[str, float | None]]] = stock.get("pl_history", [])
    if len(pl_history) < years + 1:
        return None

    recent = pl_history[: years + 1]
    eps_values: list[float] = []
    for _period, items in recent:
        eps: float | None = items.get("eps")
        if eps is None or eps <= 0:
            return None
        eps_values.append(eps)

    latest_eps = eps_values[0]
    oldest_eps = eps_values[-1]

    cagr: float = (latest_eps / oldest_eps) ** (1 / years) - 1
    if cagr <= 0:
        return None

    return per_actual / (cagr * 100)


def peg_blended_2f(stock: dict, actual_years: int) -> float | None:
    """Return blended PEG using actual EPS + 2 forecast EPS periods.

    This is **not** a standard Forward PEG.  It combines
    ``actual_years`` periods of historical EPS with ``eps_current`` and
    ``eps_next`` from the forecast, then divides ``per_next`` by the
    resulting CAGR[%].
    """
    if actual_years < 1:
        return None

    metrics: dict[str, float | None] = stock.get("metrics", {})
    per_next: float | None = metrics.get("per_next")
    if per_next is None or per_next <= 0:
        return None

    forecast: dict[str, float | None] = stock.get("forecast", {})
    eps_current: float | None = forecast.get("eps_current")
    eps_next: float | None = forecast.get("eps_next")
    if eps_current is None or eps_current <= 0:
        return None
    if eps_next is None or eps_next <= 0:
        return None

    pl_history: list[tuple[str, dict[str, float | None]]] = stock.get("pl_history", [])
    if len(pl_history) < actual_years + 1:
        return None

    recent = pl_history[: actual_years + 1]
    for _period, items in recent:
        eps: float | None = items.get("eps")
        if eps is None or eps <= 0:
            return None

    oldest_actual_eps: float | None = recent[-1][1].get("eps")
    if oldest_actual_eps is None or oldest_actual_eps <= 0:
        return None

    total_periods: int = actual_years + 2
    cagr: float = (eps_next / oldest_actual_eps) ** (1 / total_periods) - 1
    if cagr <= 0:
        return None

    return per_next / (cagr * 100)
