from __future__ import annotations

import math

import pytest

from formula_screening.indicators.peg import (
    PEG_STATUS_MISSING_INPUT,
    PEG_STATUS_NON_POSITIVE_GROWTH,
    PEG_STATUS_OK,
    peg_blended_2f,
    peg_blended_2f_with_status,
    peg_trailing,
    peg_trailing_with_status,
)


def _build_stock(
    *,
    per_actual: float | None = 10.0,
    per_next: float | None = 8.0,
    eps_values: list[float | None] | None = None,
    eps_current: float | None = 220.0,
    eps_next: float | None = 240.0,
) -> dict:
    values = eps_values or [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]
    periods = ["2025-03", "2024-03", "2023-03", "2022-03", "2021-03", "2020-03"]
    return {
        "metrics": {
            "per_actual": per_actual,
            "per_next": per_next,
        },
        "pl_history": [
            (period, {"eps": value, "net_income": value})
            for period, value in zip(periods, values)
        ],
        "forecast": {
            "eps_current": eps_current,
            "eps_next": eps_next,
        },
    }


# --- peg_trailing ---


def test_peg_trailing_uses_actual_per_divided_by_eps_cagr_percent() -> None:
    # 5-year CAGR: (200/100)^(1/5) - 1 ≈ 0.1487
    # PEG = 10.0 / (0.1487 * 100) ≈ 0.6724
    value = peg_trailing(_build_stock(), 5)

    expected_cagr = (200.0 / 100.0) ** (1 / 5) - 1
    expected_peg = 10.0 / (expected_cagr * 100)
    assert value == pytest.approx(expected_peg)
    assert peg_trailing_with_status(_build_stock(), 5).status == PEG_STATUS_OK


def test_peg_trailing_needs_years_plus_1_data_points() -> None:
    # Only 4 data points → need 4+1=5 for years=4, ok
    eps = [200.0, 180.0, 160.0, 140.0, 120.0]
    assert peg_trailing(_build_stock(eps_values=eps), 4) is not None
    # 4 data points → need 5 for years=5, fail
    assert peg_trailing(_build_stock(eps_values=eps), 5) is None


@pytest.mark.parametrize(
    ("per_actual", "eps_values"),
    [
        (None, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (0.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (10.0, [200.0, 180.0, 160.0, 140.0, 120.0]),  # not enough data
        (10.0, [200.0, 180.0, 160.0, 140.0, None, 100.0]),
        (10.0, [200.0, 180.0, 160.0, 140.0, 0.0, 100.0]),
        (10.0, [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]),  # CAGR=0
        (10.0, [80.0, 90.0, 100.0, 110.0, 120.0, 130.0]),  # negative growth
    ],
)
def test_peg_trailing_returns_none_for_invalid_inputs(
    per_actual: float | None,
    eps_values: list[float | None],
) -> None:
    value = peg_trailing(_build_stock(per_actual=per_actual, eps_values=eps_values), 5)
    assert value is None


def test_peg_trailing_distinguishes_negative_growth_from_missing_input() -> None:
    negative_growth = peg_trailing_with_status(
        _build_stock(eps_values=[80.0, 90.0, 100.0, 110.0, 120.0, 130.0]),
        5,
    )
    missing_input = peg_trailing_with_status(_build_stock(per_actual=None), 5)

    assert negative_growth.value is None
    assert negative_growth.status == PEG_STATUS_NON_POSITIVE_GROWTH
    assert missing_input.value is None
    assert missing_input.status == PEG_STATUS_MISSING_INPUT


# --- peg_blended_2f ---


def test_peg_blended_2f_uses_per_next_divided_by_blended_cagr() -> None:
    # actual_years=5 → 6 actual eps + 2 forecast = 8 data points
    # CAGR over 7 periods: (240/100)^(1/7) - 1
    stock = _build_stock()
    value = peg_blended_2f(stock, 5)

    expected_cagr = (240.0 / 100.0) ** (1 / 7) - 1
    expected_peg = 8.0 / (expected_cagr * 100)
    assert value == pytest.approx(expected_peg)
    assert peg_blended_2f_with_status(stock, 5).status == PEG_STATUS_OK


def test_peg_blended_2f_returns_none_when_actual_years_less_than_1() -> None:
    assert peg_blended_2f(_build_stock(), 0) is None


@pytest.mark.parametrize(
    ("per_next", "eps_current", "eps_next", "eps_values"),
    [
        (None, 220.0, 240.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (0.0, 220.0, 240.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (8.0, None, 240.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (8.0, 0.0, 240.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (8.0, 220.0, None, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (8.0, 220.0, 0.0, [200.0, 180.0, 160.0, 140.0, 120.0, 100.0]),
        (8.0, 220.0, 240.0, [200.0, 180.0, 160.0, 140.0, 120.0]),  # not enough
        (8.0, 220.0, 240.0, [200.0, 180.0, 160.0, 140.0, None, 100.0]),
        (8.0, 220.0, 240.0, [200.0, 180.0, 160.0, 140.0, 0.0, 100.0]),
    ],
)
def test_peg_blended_2f_returns_none_for_invalid_inputs(
    per_next: float | None,
    eps_current: float | None,
    eps_next: float | None,
    eps_values: list[float | None],
) -> None:
    stock = _build_stock(
        per_next=per_next,
        eps_current=eps_current,
        eps_next=eps_next,
        eps_values=eps_values,
    )
    assert peg_blended_2f(stock, 5) is None


def test_peg_blended_2f_returns_none_when_cagr_negative() -> None:
    # eps_next < oldest_eps → negative CAGR
    stock = _build_stock(eps_next=50.0)
    assert peg_blended_2f(stock, 5) is None
