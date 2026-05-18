from __future__ import annotations

import sys
import types
from collections.abc import Mapping

import pytest
from stock_web_ui.config import ServerConfig

import formula_screening.web as web_mod


def test_serve_screening_passes_handbook_dir_to_stock_web_ui(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_serve(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(web_mod, "_serve", fake_serve)
    monkeypatch.setattr(
        web_mod,
        "build_stock_price_metadata",
        lambda: {"price_date": "2026-05-15"},
    )

    server_config = ServerConfig(host="127.0.0.1", port=8080)
    web_mod.serve_screening([], server_config=server_config)

    api_routes = captured["api_routes"]

    assert captured["static_root"] == web_mod._STATIC_ROOT
    assert isinstance(api_routes, Mapping)
    assert set(api_routes) == {"/api/screening", "/api/stock-price-meta"}
    assert captured["server_config"] == server_config
    assert captured["yazi_base_dir"] == web_mod._HANDBOOK_DATA_DIR


def test_serialize_stock_includes_peg_trailing_5() -> None:
    payload = web_mod._serialize_stock(
        {
            "ticker": "1301",
            "name": "test",
            "price": 1000.0,
            "metrics": {
                "net_cash_ratio": 1.0,
                "per_actual": 10.0,
                "per": 5.0,
                "per_next": 6.0,
                "pbr": 0.5,
                "dividend_yield": 2.0,
                "equity_ratio": 60.0,
                "market_cap": 10000.0,
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
            "cf_history": [],
            "bs": {"stockholders_equity": 100.0, "has_preferred_shares": 1.0},
        }
    )

    expected_cagr = (200.0 / 100.0) ** (1 / 5) - 1
    expected_peg = 10.0 / (expected_cagr * 100)
    assert payload["peg_trailing_5"] == pytest.approx(expected_peg)
    assert payload["peg_trailing_5_status"] == "ok"
    assert payload["peg_blended_5y_actual_2f"] is not None
    assert payload["peg_blended_5y_actual_2f_status"] == "ok"
    assert payload["metrics"]["per_actual"] == 10.0
    assert payload["metrics"]["per_next"] == 6.0
    assert payload["has_preferred_shares"] is True


def test_serialize_stock_marks_negative_peg_growth() -> None:
    payload = web_mod._serialize_stock(
        {
            "ticker": "1999",
            "name": "test",
            "price": 1000.0,
            "metrics": {
                "per_actual": 10.0,
                "per_next": 6.0,
                "market_cap": 10000.0,
                "free_cf": 10.0,
                "interest_bearing_debt": 50.0,
            },
            "pl_history": [
                ("2025-03", {"eps": 80.0, "net_income": 80.0}),
                ("2024-03", {"eps": 90.0, "net_income": 90.0}),
                ("2023-03", {"eps": 100.0, "net_income": 100.0}),
                ("2022-03", {"eps": 110.0, "net_income": 110.0}),
                ("2021-03", {"eps": 120.0, "net_income": 120.0}),
                ("2020-03", {"eps": 130.0, "net_income": 130.0}),
            ],
            "forecast": {
                "eps_current": 70.0,
                "eps_next": 60.0,
            },
            "cf_history": [],
            "bs": {"stockholders_equity": 100.0, "has_preferred_shares": 0.0},
        }
    )

    assert payload["peg_trailing_5"] is None
    assert payload["peg_trailing_5_status"] == "non_positive_growth"
    assert payload["peg_blended_5y_actual_2f"] is None
    assert payload["peg_blended_5y_actual_2f_status"] == "non_positive_growth"


def test_serialize_stock_preserves_missing_preferred_share_flag() -> None:
    payload = web_mod._serialize_stock(
        {
            "ticker": "1301",
            "name": "test",
            "price": 1000.0,
            "metrics": {
                "market_cap": 0.0,
                "free_cf": None,
                "interest_bearing_debt": None,
            },
            "pl_history": [],
            "forecast": {},
            "cf_history": [],
            "bs": {},
        }
    )

    assert payload["has_preferred_shares"] is None


def test_compute_all_stock_metrics_exposes_preferred_share_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_core = types.ModuleType("formula_screening._core")
    fake_core.compute_all_stock_metrics = lambda _db_path: {
        "1301": {"has_preferred_shares": True}
    }
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)

    metrics = web_mod.compute_all_stock_metrics()

    assert metrics["1301"]["has_preferred_shares"] is True


def test_compute_all_stock_metrics_rejects_connection_objects() -> None:
    with pytest.raises(TypeError, match="no longer accepts sqlite connections"):
        web_mod.compute_all_stock_metrics(object())
