from __future__ import annotations

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

    server_config = ServerConfig(host="127.0.0.1", port=8080)
    web_mod.serve_screening([], server_config=server_config)

    api_routes = captured["api_routes"]

    assert captured["static_root"] == web_mod._STATIC_ROOT
    assert isinstance(api_routes, Mapping)
    assert set(api_routes) == {"/api/screening"}
    assert captured["server_config"] == server_config
    assert captured["yazi_base_dir"] == web_mod._HANDBOOK_DATA_DIR


def test_serialize_stock_includes_peg_5() -> None:
    payload = web_mod._serialize_stock(
        {
            "ticker": "1301",
            "name": "test",
            "price": 1000.0,
            "metrics": {
                "net_cash_ratio": 1.0,
                "per": 5.0,
                "per_next": 6.0,
                "per_actual": 10.0,
                "pbr": 0.5,
                "dividend_yield": 2.0,
                "equity_ratio": 60.0,
                "market_cap": 10000.0,
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
            "cf_history": [],
            "bs": {"stockholders_equity": 100.0},
        }
    )

    assert payload["peg_5"] == pytest.approx(0.5285213507883246)
    assert payload["metrics"]["per_next"] == 6.0
