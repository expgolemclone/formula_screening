"""Tests for metrics computation."""

from formula_screening.metrics import compute_metrics


def test_basic_metrics() -> None:
    financials: dict[str, dict[str, float]] = {
        "pl": {"revenue": 1000, "operating_income": 150, "net_income": 100, "basic_eps": 50},
        "bs": {"total_assets": 2000, "stockholders_equity": 800, "total_equity": 900, "total_debt": 500},
        "cf": {"operating_cf": 200, "free_cf": 120},
        "dividend": {"dps": 20},
        "forecast": {"basic_eps": 40},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["market_cap"] == 10000.0
    assert m["per"] == 25.0  # 1000 / 40 (forecast)
    assert m["per_actual"] == 20.0  # 1000 / 50 (actual)
    assert abs(m["pbr"] - 11.11) < 0.01  # 10000 / 900
    assert m["dividend_yield"] == 2.0  # 20 / 1000 * 100
    assert m["operating_margin"] == 15.0  # 150 / 1000 * 100
    assert m["roe"] == 12.5  # 100 / 800 * 100
    assert m["roa"] == 5.0  # 100 / 2000 * 100
    assert m["free_cf_ratio"] == 12.0  # 120 / 1000 * 100


def test_per_falls_to_none_when_forecast_missing() -> None:
    financials: dict[str, dict[str, float]] = {
        "pl": {"basic_eps": 50},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] is None
    assert m["per_actual"] == 20.0  # 1000 / 50


def test_per_actual_is_none_when_actual_missing() -> None:
    financials: dict[str, dict[str, float]] = {
        "forecast": {"basic_eps": 40},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] == 25.0  # 1000 / 40
    assert m["per_actual"] is None


def test_direct_values_preferred():
    """When the data source provides pre-computed ratios, use those."""
    financials = {
        "pl": {
            "revenue": 1000, "net_income": 100, "basic_eps": 50,
            "roe": 13.5, "operating_margin": 16.0,
        },
        "bs": {"total_assets": 2000, "stockholders_equity": 800, "total_equity": 900},
    }
    m = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["roe"] == 13.5
    assert m["operating_margin"] == 16.0


def test_missing_data_returns_none():
    m = compute_metrics({}, price=None, shares_outstanding=None)

    assert m["market_cap"] is None
    assert m["per"] is None
    assert m["pbr"] is None
    assert m["roe"] is None


def test_zero_denominator_returns_none() -> None:
    financials: dict[str, dict[str, float]] = {
        "pl": {"revenue": 0, "operating_income": 100, "basic_eps": 0},
        "bs": {"total_assets": 0, "stockholders_equity": 0, "total_equity": 0},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=100.0, shares_outstanding=10)

    assert m["per"] is None  # forecast_eps missing
    assert m["per_actual"] is None  # actual_eps = 0
    assert m["operating_margin"] is None  # revenue = 0
    assert m["roe"] is None  # equity = 0


def test_zero_direct_values_are_preserved() -> None:
    """IR BANK direct values of 0.0 must not fall through to computation."""
    financials: dict[str, dict[str, float]] = {
        "pl": {
            "revenue": 1000,
            "operating_income": 200,
            "ordinary_income": 300,
            "net_income": 100,
            "operating_margin": 0.0,
            "ordinary_income_margin": 0.0,
            "net_income_margin": 0.0,
            "roe": 0.0,
            "roa": 0.0,
        },
        "bs": {
            "total_assets": 2000,
            "stockholders_equity": 800,
            "total_equity": 900,
            "total_debt": 500,
            "equity_ratio": 0.0,
            "debt_equity_ratio": 0.0,
        },
        "cf": {
            "operating_cf": 200,
            "operating_cf_margin": 0.0,
        },
    }

    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["operating_margin"] == 0.0
    assert m["ordinary_margin"] == 0.0
    assert m["net_income_margin"] == 0.0
    assert m["roe"] == 0.0
    assert m["roa"] == 0.0
    assert m["equity_ratio"] == 0.0
    assert m["debt_equity_ratio"] == 0.0
    assert m["operating_cf_margin"] == 0.0
