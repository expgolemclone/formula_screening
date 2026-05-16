from __future__ import annotations

from pathlib import Path

import pytest

from formula_screening.screener import load_strategy

_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "strategies" / "net_cash_fcf.py"


def _build_stock(net_cash_ratio: float, *, has_preferred_shares: float | None = None) -> dict:
    bs = {
        "stockholders_equity": 100.0,
    }
    if has_preferred_shares is not None:
        bs["has_preferred_shares"] = has_preferred_shares

    return {
        "bs": bs,
        "metrics": {
            "net_cash_ratio": net_cash_ratio,
            "per": 5.0,
            "per_actual": 10.0,
            "per_next": 8.0,
            "equity_ratio": 60.0,
            "market_cap": 100.0,
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
        "cf_history": [
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
        ],
    }


def test_net_cash_fcf_allows_ncr_down_to_minus_one() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert strategy.screen(_build_stock(-1.0))
    assert not strategy.screen(_build_stock(-1.01))


def test_net_cash_fcf_columns_include_peg_trailing_5() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    columns = dict(strategy.columns(_build_stock(-1.0)))

    assert "peg_trailing_5" in columns
    assert "peg_blended_5y_2f" in columns


def test_net_cash_fcf_columns_show_preferred_share_flag() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert dict(strategy.columns(_build_stock(-1.0, has_preferred_shares=1.0)))["優先株"] == "あり"
    assert dict(strategy.columns(_build_stock(-1.0, has_preferred_shares=0.0)))["優先株"] == "なし"
    assert dict(strategy.columns(_build_stock(-1.0)))["優先株"] == "-"


def test_net_cash_fcf_columns_reject_invalid_preferred_share_flag() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    with pytest.raises(ValueError, match="bs.has_preferred_shares"):
        strategy.columns(_build_stock(-1.0, has_preferred_shares=2.0))
