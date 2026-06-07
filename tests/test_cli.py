from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest

import formula_screening.cli as cli_module
import formula_screening.web as web_mod
from formula_screening.stock_db_compat import PriceRefreshError


def test_cmd_screen_stops_when_price_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    strategy_path = tmp_path / "strategy.toml"
    strategy_path.write_text(
        "[[filters]]\n"
        'source = "net_cash_ratio"\n'
        'operator = ">="\n'
        "threshold = -1.0\n",
        encoding="utf-8",
    )

    def fake_ensure_prices_fresh(**kwargs: object) -> object:
        del kwargs
        raise PriceRefreshError("Yahoo failed")

    monkeypatch.setattr(cli_module, "ensure_prices_fresh", fake_ensure_prices_fresh)

    args = argparse.Namespace(
        strategy=str(strategy_path),
        ticker=None,
        workers=1,
        show_all=False,
        json=str(tmp_path / "screening.json"),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module._cmd_screen(args)

    output = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Failed to update stock prices: Yahoo failed" in output.err


def test_cmd_screen_delegates_to_rust_payload_without_reserializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_path = tmp_path / "strategy.toml"
    strategy_path.write_text(
        "[[filters]]\n"
        'source = "net_cash_ratio"\n'
        'operator = ">="\n'
        "threshold = -1.0\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "screening.json"
    gh_pages_json = tmp_path / "docs" / "assets" / "screening.json"
    gh_pages_metadata_json = tmp_path / "docs" / "assets" / "stock-price-meta.json"
    gh_pages_bs_history_dir = tmp_path / "docs" / "assets" / "bs-history"
    payload = [{"code": "1301", "metrics": {"net_cash_ratio": 1.0}}]
    captured_core: dict[str, object] = {}
    saved: list[tuple[list[dict], Path]] = []
    saved_metadata: list[Path] = []
    saved_bs_history: list[tuple[list[dict], Path]] = []

    def fake_run_screening_payload_with_diagnostics_py(
        strategy: str,
        tickers: list[str] | None,
        return_all: bool,
    ) -> dict[str, list[dict]]:
        captured_core.update(
            {
                "strategy": strategy,
                "tickers": tickers,
                "return_all": return_all,
            }
        )
        return {"payload": payload, "diagnostics": []}

    def fake_save_screening_payload_json(rows: list[dict], path: Path) -> None:
        saved.append((rows, path))

    def fake_save_stock_price_metadata_json(path: Path) -> None:
        saved_metadata.append(path)

    def fake_save_balance_sheet_history_json(rows: list[dict], path: Path) -> list[Path]:
        saved_bs_history.append((rows, path))
        return [path / "1301.json"]

    fake_core = types.ModuleType("formula_screening._core")
    fake_core.run_screening_payload_with_diagnostics_py = (
        fake_run_screening_payload_with_diagnostics_py
    )
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)
    monkeypatch.setattr(cli_module, "_GH_PAGES_JSON", gh_pages_json)
    monkeypatch.setattr(cli_module, "_GH_PAGES_METADATA_JSON", gh_pages_metadata_json)
    monkeypatch.setattr(cli_module, "_GH_PAGES_BS_HISTORY_DIR", gh_pages_bs_history_dir)
    monkeypatch.setattr(cli_module, "ensure_prices_fresh", lambda **_kwargs: None)
    monkeypatch.setattr(web_mod, "save_screening_payload_json", fake_save_screening_payload_json)
    monkeypatch.setattr(web_mod, "save_stock_price_metadata_json", fake_save_stock_price_metadata_json)
    monkeypatch.setattr(web_mod, "save_balance_sheet_history_json", fake_save_balance_sheet_history_json)

    args = argparse.Namespace(
        strategy=str(strategy_path),
        ticker=["1301", "7203"],
        workers=8,
        show_all=True,
        json=str(json_path),
    )

    cli_module._cmd_screen(args)

    assert captured_core == {
        "strategy": str(strategy_path),
        "tickers": ["1301", "7203"],
        "return_all": True,
    }
    assert saved == [(payload, gh_pages_json), (payload, json_path)]
    assert saved_metadata == [gh_pages_metadata_json]
    assert saved_bs_history == [(payload, gh_pages_bs_history_dir)]


def test_cmd_screen_logs_missing_fields_and_keeps_writing_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    strategy_path = tmp_path / "strategy.toml"
    strategy_path.write_text(
        "[[filters]]\n"
        'source = "net_cash_ratio"\n'
        'operator = ">="\n'
        "threshold = -1.0\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "screening.json"
    gh_pages_json = tmp_path / "docs" / "assets" / "screening.json"
    gh_pages_metadata_json = tmp_path / "docs" / "assets" / "stock-price-meta.json"
    gh_pages_bs_history_dir = tmp_path / "docs" / "assets" / "bs-history"
    payload = [{"code": "1301", "metrics": {"net_cash_ratio": None}}]
    diagnostics = [
        {
            "code": "1301",
            "name": "test stock",
            "missing_fields": ["metrics.net_cash_ratio", "fcf_yield_avg"],
        }
    ]
    saved: list[tuple[list[dict], Path]] = []
    saved_metadata: list[Path] = []
    saved_bs_history: list[tuple[list[dict], Path]] = []

    def fake_save_screening_payload_json(rows: list[dict], path: Path) -> None:
        saved.append((rows, path))

    def fake_save_stock_price_metadata_json(path: Path) -> None:
        saved_metadata.append(path)

    def fake_save_balance_sheet_history_json(rows: list[dict], path: Path) -> list[Path]:
        saved_bs_history.append((rows, path))
        return [path / "1301.json"]

    fake_core = types.ModuleType("formula_screening._core")
    fake_core.run_screening_payload_with_diagnostics_py = lambda *_args: {
        "payload": payload,
        "diagnostics": diagnostics,
    }
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)
    monkeypatch.setattr(cli_module, "_GH_PAGES_JSON", gh_pages_json)
    monkeypatch.setattr(cli_module, "_GH_PAGES_METADATA_JSON", gh_pages_metadata_json)
    monkeypatch.setattr(cli_module, "_GH_PAGES_BS_HISTORY_DIR", gh_pages_bs_history_dir)
    monkeypatch.setattr(cli_module, "ensure_prices_fresh", lambda **_kwargs: None)
    monkeypatch.setattr(web_mod, "save_screening_payload_json", fake_save_screening_payload_json)
    monkeypatch.setattr(web_mod, "save_stock_price_metadata_json", fake_save_stock_price_metadata_json)
    monkeypatch.setattr(web_mod, "save_balance_sheet_history_json", fake_save_balance_sheet_history_json)

    args = argparse.Namespace(
        strategy=str(strategy_path),
        ticker=["1301"],
        workers=1,
        show_all=False,
        json=str(json_path),
    )

    with caplog.at_level("ERROR", logger="formula_screening.cli"):
        cli_module._cmd_screen(args)

    assert "Missing screening fields for 1301 (test stock): metrics.net_cash_ratio, fcf_yield_avg" in caplog.text
    assert saved == [(payload, gh_pages_json), (payload, json_path)]
    assert saved_metadata == [gh_pages_metadata_json]
    assert saved_bs_history == [(payload, gh_pages_bs_history_dir)]
