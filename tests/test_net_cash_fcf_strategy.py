from __future__ import annotations

from pathlib import Path

from formula_screening.screener import load_strategy

_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "strategies" / "net_cash_fcf.py"


def _build_stock(net_cash_ratio: float) -> dict:
    return {
        "metrics": {
            "net_cash_ratio": net_cash_ratio,
            "per": 5.0,
            "equity_ratio": 60.0,
            "market_cap": 100.0,
        },
        "cf_history": [("2025-03", {"free_cf": 10.0})],
    }


def test_net_cash_fcf_allows_ncr_down_to_minus_one() -> None:
    strategy = load_strategy(_STRATEGY_PATH)

    assert strategy.screen(_build_stock(-1.0))
    assert not strategy.screen(_build_stock(-1.01))
