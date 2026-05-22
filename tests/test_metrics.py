from __future__ import annotations

import pytest

from formula_screening.metrics import compute_metrics


def _base_financials() -> dict:
    return {
        "pl": {
            "revenue": 100_000_000_000.0,
            "operating_income": 10_000_000_000.0,
            "ordinary_income": 9_000_000_000.0,
            "net_income": 6_000_000_000.0,
        },
        "bs": {
            "total_assets": 50_000_000_000.0,
            "stockholders_equity": 25_000_000_000.0,
            "total_equity": 25_000_000_000.0,
            "total_debt": 10_000_000_000.0,
            "current_assets": 20_000_000_000.0,
            "current_liabilities": 8_000_000_000.0,
            "non_current_liabilities": 5_000_000_000.0,
        },
        "cf": {
            "operating_cf": 8_000_000_000.0,
            "investing_cf": -3_000_000_000.0,
        },
        "dividend": {"dps": 50.0},
        "forecast": {
            "net_income_current": 7_000_000_000.0,
            "net_income_next": 8_000_000_000.0,
        },
    }


def test_per_uses_current_forecast() -> None:
    metrics = compute_metrics(_base_financials(), price=1000.0, shares_outstanding=10_000_000)
    market_cap = 1000.0 * 10_000_000
    assert metrics["per"] == pytest.approx(market_cap / 7_000_000_000.0)


def test_per_next_uses_next_forecast() -> None:
    metrics = compute_metrics(_base_financials(), price=1000.0, shares_outstanding=10_000_000)
    market_cap = 1000.0 * 10_000_000
    assert metrics["per_next"] == pytest.approx(market_cap / 8_000_000_000.0)


def test_per_actual_unchanged() -> None:
    metrics = compute_metrics(_base_financials(), price=1000.0, shares_outstanding=10_000_000)
    market_cap = 1000.0 * 10_000_000
    assert metrics["per_actual"] == pytest.approx(market_cap / 6_000_000_000.0)


def test_per_none_when_no_forecast() -> None:
    financials = _base_financials()
    financials["forecast"] = {}
    metrics = compute_metrics(financials, price=1000.0, shares_outstanding=10_000_000)
    assert metrics["per"] is None
    assert metrics["per_next"] is None


def test_per_next_none_when_only_current() -> None:
    financials = _base_financials()
    financials["forecast"] = {"net_income_current": 7_000_000_000.0}
    metrics = compute_metrics(financials, price=1000.0, shares_outstanding=10_000_000)
    assert metrics["per"] is not None
    assert metrics["per_next"] is None


def test_net_cash_ratio_subtracts_current_and_non_current_liabilities() -> None:
    metrics = compute_metrics(_base_financials(), price=1000.0, shares_outstanding=10_000_000)

    assert metrics["net_cash"] == pytest.approx(7_000_000_000.0)
    assert metrics["net_cash_ratio"] == pytest.approx(0.7)
