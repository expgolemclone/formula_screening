from __future__ import annotations

import pytest

from formula_screening.indicators import peg_5


def _build_stock(
    *,
    per_actual: float | None = 10.0,
    net_incomes: list[float | None] | None = None,
) -> dict:
    values = net_incomes or [200.0, 180.0, 160.0, 140.0, 100.0]
    periods = ["2025-03", "2024-03", "2023-03", "2022-03", "2021-03"]
    return {
        "metrics": {
            "per_actual": per_actual,
        },
        "pl_history": [
            (period, {"net_income": value})
            for period, value in zip(periods, values)
        ],
    }


def test_peg_5_uses_actual_per_divided_by_cagr_percent() -> None:
    value = peg_5(_build_stock())

    assert value == pytest.approx(0.5285213507883246)


@pytest.mark.parametrize(
    ("per_actual", "net_incomes"),
    [
        (None, [200.0, 180.0, 160.0, 140.0, 100.0]),
        (0.0, [200.0, 180.0, 160.0, 140.0, 100.0]),
        (10.0, [200.0, 180.0, 160.0, 140.0]),
        (10.0, [200.0, 180.0, 160.0, 140.0, None]),
        (10.0, [200.0, 180.0, 160.0, 140.0, 0.0]),
        (10.0, [100.0, 100.0, 100.0, 100.0, 100.0]),
        (10.0, [80.0, 90.0, 100.0, 110.0, 120.0]),
    ],
)
def test_peg_5_returns_none_for_invalid_inputs(
    per_actual: float | None,
    net_incomes: list[float | None],
) -> None:
    value = peg_5(_build_stock(per_actual=per_actual, net_incomes=net_incomes))

    assert value is None
