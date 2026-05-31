"""FCF growth rate indicators: exponential regression CAGR, R², and SMA CAGR."""

from __future__ import annotations

import math

from formula_screening.config import MAGIC

_FCF_YEARS: int = MAGIC["screening"]["fcf_years"]
_SMA_WINDOW: int = MAGIC["screening"]["fcf_sma_window"]


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


def _collect_fcf_values(stock: dict, years: int) -> list[float] | None:
    """Collect FCF values from cf_history (oldest first). Returns None if insufficient data."""
    cf_history: list[tuple[str, dict[str, float | None]]] = stock.get("cf_history", [])
    values: list[float | None] = [
        _resolve_free_cf(cf) for _, cf in cf_history[:years]
    ]
    if any(v is None for v in values):
        return None
    # Reverse to oldest-first
    return list(reversed([v for v in values if v is not None]))


def _linreg_slope_r2(y_values: list[float]) -> tuple[float, float] | None:
    """Simple linear regression y = a + bx (x = 0,1,...,n-1). Returns (slope, R²)."""
    n = len(y_values)
    if n < 2:
        return None
    s_x = sum(float(i) for i in range(n))
    s_y = sum(y_values)
    s_xx = sum(float(i * i) for i in range(n))
    s_xy = sum(float(i) * y for i, y in enumerate(y_values))
    s_yy = sum(y * y for y in y_values)
    denom = n * s_xx - s_x * s_x
    if denom == 0.0:
        return None
    slope = (n * s_xy - s_x * s_y) / denom
    denom_r2 = (n * s_xx - s_x * s_x) * (n * s_yy - s_y * s_y)
    if denom_r2 <= 0.0:
        return (slope, 0.0)
    r2 = (n * s_xy - s_x * s_y) ** 2 / denom_r2
    return (slope, r2)


def _linear_cagr_pct(values: list[float]) -> float | None:
    """Linear regression growth rate: slope / |mean| * 100.

    Returns None if mean is zero (degenerate case).
    """
    result = _linreg_slope_r2(values)
    if result is None:
        return None
    slope, _ = result
    mean = sum(values) / len(values)
    if mean == 0.0:
        return None
    return (slope / abs(mean)) * 100


def fcf_cagr(stock: dict, years: int = _FCF_YEARS) -> float | None:
    """CAGR of FCF over *years* periods (%).

    When all FCF values are positive, uses exponential regression (compound CAGR).
    When any FCF <= 0, falls back to linear regression growth rate (slope / |mean| * 100).
    Returns None if insufficient data.
    """
    values = _collect_fcf_values(stock, years)
    if values is None:
        return None
    if all(v > 0 for v in values):
        log_values = [math.log(v) for v in values]
        result = _linreg_slope_r2(log_values)
        if result is None:
            return None
        slope, _ = result
        return (math.exp(slope) - 1) * 100
    return _linear_cagr_pct(values)


def fcf_cagr_r2(stock: dict, years: int = _FCF_YEARS) -> float | None:
    """R² of FCF regression (0.0 ~ 1.0).

    When all FCF values are positive, uses exponential regression R².
    When any FCF <= 0, uses linear regression R² on raw values.
    Returns None if insufficient data.
    """
    values = _collect_fcf_values(stock, years)
    if values is None:
        return None
    if all(v > 0 for v in values):
        log_values = [math.log(v) for v in values]
        reg_values = log_values
    else:
        reg_values = values
    result = _linreg_slope_r2(reg_values)
    if result is None:
        return None
    _, r2 = result
    return r2


def fcf_sma_cagr(
    stock: dict, years: int = _FCF_YEARS, sma_window: int = _SMA_WINDOW
) -> float | None:
    """SMA-smoothed CAGR of FCF over *years* periods (%).

    Works with negative FCF values (as long as SMA endpoints are positive).
    """
    values = _collect_fcf_values(stock, years)
    if values is None or len(values) < sma_window:
        return None
    sma_count = len(values) - sma_window + 1
    if sma_count < 2:
        return None
    sma_values: list[float] = []
    for i in range(sma_count):
        avg = sum(values[i : i + sma_window]) / sma_window
        sma_values.append(avg)
    first = sma_values[0]
    last = sma_values[-1]
    n_years = sma_count - 1
    if first > 0 and last > 0:
        return (last / first) ** (1.0 / n_years) - 1
    # Fallback for non-positive SMA endpoints: linear growth rate
    if first == 0.0:
        return None
    return (last - first) / abs(first) / n_years
