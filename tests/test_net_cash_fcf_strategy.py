from __future__ import annotations

from pathlib import Path

import pytest

from formula_screening.screener import load_strategy

_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "strategies" / "net_cash_fcf.toml"


def _build_stock(
    net_cash_ratio: float,
    *,
    fcf_values: list[float] | None = None,
    has_preferred_shares: float | None = None,
) -> dict:
    bs = {
        "stockholders_equity": 100.0,
    }
    if has_preferred_shares is not None:
        bs["has_preferred_shares"] = has_preferred_shares
    cf_history = [
        ("2025-03", {"free_cf": 10.0}),
        ("2024-03", {"free_cf": 9.0}),
        ("2023-03", {"free_cf": 8.0}),
        ("2022-03", {"free_cf": 7.0}),
        ("2021-03", {"free_cf": 6.0}),
        ("2020-03", {"free_cf": 5.0}),
        ("2019-03", {"free_cf": 4.0}),
        ("2018-03", {"free_cf": 3.0}),
        ("2017-03", {"free_cf": 2.0}),
        ("2016-03", {"free_cf": 1.0}),
    ]
    if fcf_values is not None:
        cf_history = [
            (f"{2025 - idx}-03", {"free_cf": value})
            for idx, value in enumerate(fcf_values)
        ]

    return {
        "bs": bs,
        "metrics": {
            "net_cash_ratio": net_cash_ratio,
            "per": 5.0,
            "per_actual": 10.0,
            "per_next": 8.0,
            "equity_ratio": 60.0,
            "market_cap": 100.0,
            "retained_earnings_ratio": 0.4,
            "free_cf": 10.0,
            "interest_bearing_debt": 50.0,
        },
        "pl_history": [
            ("2025-03", {"eps": 200.0, "net_income": 200.0}),
            ("2024-03", {"eps": 180.0, "net_income": 180.0}),
            ("2023-03", {"eps": 160.0, "net_income": 160.0}),
            ("2022-03", {"eps": 140.0, "net_income": 140.0}),
            ("2021-03", {"eps": 120.0, "net_income": 120.0}),
            ("2020-03", {"eps": 100.0, "net_income": 100.0}),
        ],
        "forecast": {
            "eps_current": 220.0,
            "eps_next": 240.0,
        },
        "cf_history": cf_history,
        "potential_equity_summary": {
            "has_potential_equity": None,
            "total_potential_common_shares": None,
            "has_unquantified_terms": False,
            "instrument_types": [],
        },
    }


def test_net_cash_fcf_requires_net_cash_ratio_at_least_seventy_percent() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert strategy.screen(_build_stock(0.7))
    assert not strategy.screen(_build_stock(0.699))


def test_net_cash_fcf_requires_fcf_yield_at_least_five_percent() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert strategy.screen(_build_stock(0.7, fcf_values=[5.0] * 10))
    assert not strategy.screen(_build_stock(0.7, fcf_values=[4.99] * 10))


def test_net_cash_fcf_columns_include_peg_trailing_5() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    columns = dict(strategy.columns(_build_stock(-1.0)))

    assert "peg_5y" in columns
    assert "peg_5y2f" in columns
    assert columns["re/mcap"] == "0.40"


def test_net_cash_fcf_columns_include_preferred_share_as_web_bool() -> None:
    """preferred_share is now a web-only bool column, not a CLI text column."""
    strategy = load_strategy(_STRATEGY_PATH)

    # CLI columns should not include the web-only bool column
    columns = dict(strategy.columns(_build_stock(-1.0, has_preferred_shares=1.0)))
    assert "優先株" not in columns


def test_load_strategy_rejects_python_strategy_files(tmp_path: Path) -> None:
    strategy_path = tmp_path / "legacy.py"
    strategy_path.write_text("FILTERS = []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be TOML"):
        load_strategy(strategy_path)
