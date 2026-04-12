"""Tests for metrics computation."""

from formula_screening.metrics import compute_metrics


def _financials(**overrides: dict) -> dict[str, dict]:
    """Build a financials dict with all statement keys guaranteed."""
    base: dict[str, dict] = {
        "pl": {}, "bs": {}, "cf": {}, "dividend": {}, "ss": {}, "forecast": {},
    }
    base.update(overrides)
    return base


def test_basic_metrics() -> None:
    financials = _financials(
        pl={"revenue": 1000, "operating_income": 150, "net_income": 500},
        bs={"total_assets": 2000, "stockholders_equity": 800, "total_equity": 900, "total_debt": 500},
        cf={"operating_cf": 200, "free_cf": 120},
        dividend={"dps": 20},
        forecast={"net_income": 400},
    )
    m = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["market_cap"] == 10000.0
    assert m["per"] == 25.0  # 10000 / 400
    assert m["per_actual"] == 20.0  # 10000 / 500
    assert abs(m["pbr"] - 11.11) < 0.01  # 10000 / 900
    assert m["dividend_yield"] == 2.0  # 20 / 1000 * 100
    assert m["operating_margin"] == 15.0  # 150 / 1000 * 100
    assert m["roe"] == 62.5  # 500 / 800 * 100
    assert m["roa"] == 25.0  # 500 / 2000 * 100
    assert m["free_cf_ratio"] == 12.0  # 120 / 1000 * 100


def test_per_falls_to_none_when_forecast_net_income_missing() -> None:
    financials = _financials(
        pl={"net_income": 500},
    )
    m = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] is None
    assert m["per_actual"] == 20.0  # 10000 / 500


def test_per_actual_is_none_when_pl_net_income_missing() -> None:
    financials = _financials(
        forecast={"net_income": 400},
    )
    m = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] == 25.0  # 10000 / 400
    assert m["per_actual"] is None


def test_metrics_always_computed_from_raw_data() -> None:
    """Metrics are calculated from raw components, not from pre-computed values."""
    financials = _financials(
        pl={
            "revenue": 1000, "net_income": 100,
            "roe": 13.5, "operating_margin": 16.0,
        },
        bs={"total_assets": 2000, "stockholders_equity": 800, "total_equity": 900},
    )
    m = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["roe"] == 12.5  # 100 / 800 * 100 (computed, not 13.5)
    assert m["operating_margin"] is None  # no operating_income → None


def test_missing_data_returns_none() -> None:
    m = compute_metrics(_financials(), price=None, shares_outstanding=None)

    assert m["market_cap"] is None
    assert m["per"] is None
    assert m["pbr"] is None
    assert m["roe"] is None


def test_zero_denominator_returns_none() -> None:
    financials = _financials(
        pl={"revenue": 0, "operating_income": 100, "net_income": 0},
        bs={"total_assets": 0, "stockholders_equity": 0, "total_equity": 0},
    )
    m = compute_metrics(financials, price=100.0, shares_outstanding=10)

    assert m["per"] is None  # forecast.net_income missing
    assert m["per_actual"] is None  # pl.net_income = 0
    assert m["operating_margin"] is None  # revenue = 0
    assert m["roe"] is None  # equity = 0


def test_free_cf_derived_from_operating_and_investing() -> None:
    """When free_cf is absent, derive from operating_cf + investing_cf."""
    financials = _financials(
        cf={"operating_cf": 300, "investing_cf": -100},
    )
    m = compute_metrics(financials, price=100.0, shares_outstanding=10)

    assert m["free_cf"] == 200.0
    assert m["free_cf_ratio"] is None  # revenue is None


def test_free_cf_prefers_explicit_value() -> None:
    """When free_cf is present in CF data, use it directly."""
    financials = _financials(
        cf={"free_cf": 50, "operating_cf": 300, "investing_cf": -100},
    )
    m = compute_metrics(financials, price=100.0, shares_outstanding=10)

    assert m["free_cf"] == 50


def test_interest_bearing_debt_requires_both_components() -> None:
    """Both short_term_debt and long_term_debt must be present."""
    financials = _financials(
        bs={"short_term_debt": 100},
    )
    m = compute_metrics(financials, price=100.0, shares_outstanding=10)
    assert m["interest_bearing_debt"] is None

    financials2 = _financials(
        bs={"short_term_debt": 100, "long_term_debt": 200},
    )
    m2 = compute_metrics(financials2, price=100.0, shares_outstanding=10)
    assert m2["interest_bearing_debt"] == 300


def test_net_cash_requires_core_components() -> None:
    """Net cash requires current_assets, current_liabilities, non_current_liabilities."""
    financials = _financials(
        bs={
            "current_assets": 1000,
            "current_liabilities": 300,
            "non_current_liabilities": 200,
            "inventories": 100,
            "investment_securities": 50,
        },
    )
    m = compute_metrics(financials, price=100.0, shares_outstanding=10)

    # 1000 - 500 - 100 + 50*0.7 = 435
    assert m["net_cash"] == 435.0

    # Missing non_current_liabilities → None
    financials2 = _financials(
        bs={"current_assets": 1000, "current_liabilities": 300},
    )
    m2 = compute_metrics(financials2, price=100.0, shares_outstanding=10)
    assert m2["net_cash"] is None
