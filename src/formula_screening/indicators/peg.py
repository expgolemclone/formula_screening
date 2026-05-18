"""PEG ratio indicators: trailing PEG and blended 2-forecast PEG.

Both functions use EPS (not net_income) for the standard PEG definition.
EPS data comes from stock_db's ``compute_eps`` CLI, stored as
``pl.item_name='eps'``, ``forecast.item_name='eps_current'/'eps_next'``.
"""

from __future__ import annotations

from dataclasses import dataclass

PEG_STATUS_OK = "ok"
PEG_STATUS_MISSING_INPUT = "missing_input"
PEG_STATUS_INSUFFICIENT_HISTORY = "insufficient_history"
PEG_STATUS_NON_POSITIVE_PER = "non_positive_per"
PEG_STATUS_NON_POSITIVE_EPS = "non_positive_eps"
PEG_STATUS_NON_POSITIVE_GROWTH = "non_positive_growth"


@dataclass(frozen=True)
class PegResult:
    value: float | None
    status: str


def peg_trailing(stock: dict, years: int) -> float | None:
    """Return Trailing PEG: ``per_actual / EPS CAGR[%]`` over *years* periods.

    Uses ``pl_history[:years + 1]`` EPS values so that a 5-year CAGR needs
    6 data points.
    """
    return peg_trailing_with_status(stock, years).value


def peg_trailing_with_status(stock: dict, years: int) -> PegResult:
    """Return trailing PEG plus a reason status when the value is unavailable."""
    if years < 1:
        return PegResult(None, PEG_STATUS_INSUFFICIENT_HISTORY)

    metrics: dict[str, float | None] = stock.get("metrics", {})
    per_actual: float | None = metrics.get("per_actual")
    if per_actual is None:
        return PegResult(None, PEG_STATUS_MISSING_INPUT)
    if per_actual <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_PER)

    pl_history: list[tuple[str, dict[str, float | None]]] = stock.get("pl_history", [])
    if len(pl_history) < years + 1:
        return PegResult(None, PEG_STATUS_INSUFFICIENT_HISTORY)

    recent = pl_history[: years + 1]
    eps_values: list[float] = []
    for _period, items in recent:
        eps: float | None = items.get("eps")
        if eps is None:
            return PegResult(None, PEG_STATUS_MISSING_INPUT)
        if eps <= 0:
            return PegResult(None, PEG_STATUS_NON_POSITIVE_EPS)
        eps_values.append(eps)

    latest_eps = eps_values[0]
    oldest_eps = eps_values[-1]

    cagr: float = (latest_eps / oldest_eps) ** (1 / years) - 1
    if cagr <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_GROWTH)

    return PegResult(per_actual / (cagr * 100), PEG_STATUS_OK)


def peg_blended_2f(stock: dict, actual_years: int) -> float | None:
    """Return blended PEG using actual EPS + 2 forecast EPS periods.

    This is **not** a standard Forward PEG.  It combines
    ``actual_years`` periods of historical EPS with ``eps_current`` and
    ``eps_next`` from the forecast, then divides ``per_next`` by the
    resulting CAGR[%].
    """
    return peg_blended_2f_with_status(stock, actual_years).value


def peg_blended_2f_with_status(stock: dict, actual_years: int) -> PegResult:
    """Return blended PEG plus a reason status when the value is unavailable."""
    if actual_years < 1:
        return PegResult(None, PEG_STATUS_INSUFFICIENT_HISTORY)

    metrics: dict[str, float | None] = stock.get("metrics", {})
    per_next: float | None = metrics.get("per_next")
    if per_next is None:
        return PegResult(None, PEG_STATUS_MISSING_INPUT)
    if per_next <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_PER)

    forecast: dict[str, float | None] = stock.get("forecast", {})
    eps_current: float | None = forecast.get("eps_current")
    eps_next: float | None = forecast.get("eps_next")
    if eps_current is None or eps_next is None:
        return PegResult(None, PEG_STATUS_MISSING_INPUT)
    if eps_current <= 0 or eps_next <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_EPS)

    pl_history: list[tuple[str, dict[str, float | None]]] = stock.get("pl_history", [])
    if len(pl_history) < actual_years + 1:
        return PegResult(None, PEG_STATUS_INSUFFICIENT_HISTORY)

    recent = pl_history[: actual_years + 1]
    for _period, items in recent:
        eps: float | None = items.get("eps")
        if eps is None:
            return PegResult(None, PEG_STATUS_MISSING_INPUT)
        if eps <= 0:
            return PegResult(None, PEG_STATUS_NON_POSITIVE_EPS)

    oldest_actual_eps: float | None = recent[-1][1].get("eps")
    if oldest_actual_eps is None:
        return PegResult(None, PEG_STATUS_MISSING_INPUT)
    if oldest_actual_eps <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_EPS)

    total_periods: int = actual_years + 2
    cagr: float = (eps_next / oldest_actual_eps) ** (1 / total_periods) - 1
    if cagr <= 0:
        return PegResult(None, PEG_STATUS_NON_POSITIVE_GROWTH)

    return PegResult(per_next / (cagr * 100), PEG_STATUS_OK)
