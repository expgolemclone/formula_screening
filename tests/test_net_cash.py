from __future__ import annotations

import pytest

from formula_screening.net_cash import compute_net_cash_metrics


def test_compute_net_cash_metrics_matches_yoshicon_example() -> None:
    metrics = compute_net_cash_metrics(
        {
            "current_assets": 38_675_872_000.0,
            "inventories": 32_983_204_000.0,
            "investment_securities": 2_985_654_000.0,
            "current_liabilities": 15_158_894_000.0,
            "non_current_liabilities": 1_468_637_000.0,
        },
        price=2567.0,
        shares_outstanding=8_030_248,
    )

    assert metrics["market_cap"] == pytest.approx(20_613_646_616.0)
    assert metrics["net_cash"] == pytest.approx(-8_844_905_200.0)
    assert metrics["net_cash_ratio"] == pytest.approx(-0.42908008295508077)
