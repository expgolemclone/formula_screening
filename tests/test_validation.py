from __future__ import annotations

import pytest

from formula_screening import validation as validation_mod
from formula_screening.validation import (
    build_net_cash_snapshot,
    load_latest_bs,
    select_validation_targets,
)


def test_select_validation_targets_orders_by_market_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation_mod.stock_db_api,
        "get_validation_targets",
        lambda limit: [
            {
                "ticker": "2222",
                "name": "Beta",
                "securities_report_url": "https://example.com/b.pdf",
                "price": 300.0,
                "shares_outstanding": 50,
            },
            {
                "ticker": "1111",
                "name": "Alpha",
                "securities_report_url": "https://example.com/a.pdf",
                "price": 100.0,
                "shares_outstanding": 100,
            },
        ][:limit],
    )

    targets = select_validation_targets(None, 2)

    assert [target.ticker for target in targets] == ["2222", "1111"]


def test_load_latest_bs_returns_stock_db_api_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation_mod.stock_db_api,
        "get_latest_balance_sheet",
        lambda ticker: (
            "2025-03",
            {"current_assets": 38_675_872_000, "inventories": 32_974_467_000},
            None,
        ),
    )

    period, bs, status = load_latest_bs(None, "8888")

    assert period == "2025-03"
    assert status is None
    assert bs["inventories"] == 32_974_467_000


def test_load_latest_bs_returns_missing_when_xbrl_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation_mod.stock_db_api,
        "get_latest_balance_sheet",
        lambda ticker: (None, {}, "scrape_missing"),
    )

    period, bs, status = load_latest_bs(None, "5280")

    assert period is None
    assert status == "scrape_missing"
    assert bs == {}


def test_load_latest_bs_propagates_status_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation_mod.stock_db_api,
        "get_latest_balance_sheet",
        lambda ticker: (None, {}, "scrape_blocked"),
    )

    period, bs, status = load_latest_bs(None, "7000")

    assert period is None
    assert status == "scrape_blocked"
    assert bs == {}


def test_build_net_cash_snapshot_computes_ratio() -> None:
    snapshot = build_net_cash_snapshot(
        "2025-03",
        {
            "current_assets": 38_675_872_000.0,
            "inventories": 32_974_467_000.0,
            "investment_securities": 2_985_654_000.0,
            "current_liabilities": 15_158_894_000.0,
            "non_current_liabilities": 1_468_637_000.0,
        },
        price=2567.0,
        shares_outstanding=8_030_248,
    )

    assert snapshot.period == "2025-03"
    assert snapshot.net_cash is not None
    assert snapshot.market_cap is not None
    assert snapshot.net_cash_ratio is not None
