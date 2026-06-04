from __future__ import annotations

import sys
import types
import json
from collections.abc import Mapping
from pathlib import Path

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
        lambda: {"price_date": "2026-05-20", "target_price_date": "2026-05-20"},
    )

    server_config = ServerConfig(host="127.0.0.1", port=8080)
    web_mod.serve_screening([], server_config=server_config)

    api_routes = captured["api_routes"]

    assert captured["static_root"] == web_mod._STATIC_ROOT
    assert isinstance(api_routes, Mapping)
    assert set(api_routes) == {"/api/screening", "/api/stock-price-meta"}
    assert captured["server_config"] == server_config
    assert captured["yazi_base_dir"] == web_mod._HANDBOOK_DATA_DIR


def test_build_stock_price_metadata_uses_stock_db_api(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"price_date": "2026-05-20", "target_price_date": "2026-05-20"}
    monkeypatch.setattr(web_mod, "get_stock_price_metadata", lambda: expected)

    assert web_mod.build_stock_price_metadata() == expected


def test_save_stock_price_metadata_json_writes_price_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "assets" / "stock-price-meta.json"
    monkeypatch.setattr(
        web_mod,
        "get_stock_price_metadata",
        lambda: {"price_date": "2026-05-20", "target_price_date": "2026-05-20"},
    )

    web_mod.save_stock_price_metadata_json(output_path)

    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    assert metadata == {
        "price_date": "2026-05-20",
        "target_price_date": "2026-05-20",
    }


def test_serialize_stock_includes_peg_trailing_5() -> None:
    payload = web_mod._serialize_stock(
        {
            "ticker": "1301",
            "name": "test",
            "price": 1000.0,
            "price_date": "2026-05-20",
            "metrics": {
                "net_cash_ratio": 1.0,
                "per_actual": 10.0,
                "per": 5.0,
                "per_next": 6.0,
                "pbr": 0.5,
                "dividend_yield": 2.0,
                "total_payout_ratio": 30.0,
                "retained_earnings_ratio": 0.4,
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
            "potential_equity_summary": {
                "has_potential_equity": True,
                "total_potential_common_shares": 10_000.0,
                "has_unquantified_terms": True,
                "instrument_types": ["share_acquisition_right"],
            },
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
    assert payload["metrics"]["total_payout_ratio"] == 30.0
    assert payload["metrics"]["retained_earnings_ratio"] == 0.4
    assert payload["price_date"] == "2026-05-20"
    assert payload["has_preferred_shares"] is True
    assert payload["has_potential_equity"] is True
    assert payload["potential_common_shares"] == 10_000.0
    assert payload["has_unquantified_potential_equity"] is True


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
            "potential_equity_summary": {
                "has_potential_equity": None,
                "total_potential_common_shares": None,
                "has_unquantified_terms": False,
                "instrument_types": [],
            },
        }
    )

    assert payload["has_preferred_shares"] is None


def test_compute_all_stock_metrics_exposes_preferred_share_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_core = types.ModuleType("formula_screening._core")
    fake_core.compute_all_stock_metrics = lambda: {"1301": {"has_preferred_shares": True}}
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)

    metrics = web_mod.compute_all_stock_metrics()

    assert metrics["1301"]["has_preferred_shares"] is True


def test_compute_all_stock_metrics_rejects_connection_objects() -> None:
    with pytest.raises(TypeError, match="no longer accepts sqlite connections"):
        web_mod.compute_all_stock_metrics(object())


def test_run_screening_strategy_payload_uses_public_rust_payload_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_path = tmp_path / "strategy.toml"
    expected_payload = [{"code": "1301"}]
    captured: dict[str, object] = {}

    def fake_run_screening_payload_py(
        strategy: str,
        tickers: list[str] | None,
        return_all: bool,
    ) -> list[dict]:
        captured.update(
            {
                "strategy": strategy,
                "tickers": tickers,
                "return_all": return_all,
            }
        )
        return expected_payload

    fake_core = types.ModuleType("formula_screening._core")
    fake_core.run_screening_payload_py = fake_run_screening_payload_py
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)

    payload = web_mod.run_screening_strategy_payload(
        strategy_path,
        tickers=("1301", "7203"),
        return_all=True,
    )

    assert payload == expected_payload
    assert captured == {
        "strategy": str(strategy_path),
        "tickers": ["1301", "7203"],
        "return_all": True,
    }
