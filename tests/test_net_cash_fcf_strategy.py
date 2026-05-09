from __future__ import annotations

from pathlib import Path

from formula_screening.screener import load_strategy

_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "strategies" / "net_cash_fcf.py"


def _build_stock(net_cash_ratio: float) -> dict:
    return {
        "bs": {
            "stockholders_equity": 100.0,
        },
        "metrics": {
            "net_cash_ratio": net_cash_ratio,
            "per": 5.0,
            "per_actual": 10.0,
            "equity_ratio": 60.0,
            "market_cap": 100.0,
            "free_cf": 10.0,
            "interest_bearing_debt": 50.0,
        },
        "pl_history": [
            ("2025-03", {"net_income": 200.0}),
            ("2024-03", {"net_income": 180.0}),
            ("2023-03", {"net_income": 160.0}),
            ("2022-03", {"net_income": 140.0}),
            ("2021-03", {"net_income": 100.0}),
        ],
        "cf_history": [("2025-03", {"free_cf": 10.0})],
    }


def test_net_cash_fcf_allows_ncr_down_to_minus_one() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert strategy.screen(_build_stock(-1.0))
    assert not strategy.screen(_build_stock(-1.01))


def test_net_cash_fcf_columns_include_peg_5() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    columns = dict(strategy.columns(_build_stock(-1.0)))

    assert columns["peg_5"] == "0.53"
