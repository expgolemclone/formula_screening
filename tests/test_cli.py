from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest

import formula_screening.cli as cli_module
import formula_screening.web as web_mod
from stock_db.sources.stooq import StooqDailyPriceUpdateError


def test_cmd_screen_stops_when_stooq_update_fails(
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

    def fake_ensure_stooq_prices_fresh(**kwargs: object) -> object:
        del kwargs
        raise StooqDailyPriceUpdateError("Unauthorized")

    monkeypatch.setattr(cli_module, "ensure_stooq_prices_fresh", fake_ensure_stooq_prices_fresh)

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
    assert "Failed to update Stooq prices: Unauthorized" in output.err


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
    db_path = tmp_path / "stocks.db"
    json_path = tmp_path / "screening.json"
    gh_pages_json = tmp_path / "docs" / "assets" / "screening.json"
    payload = [{"code": "1301", "metrics": {"net_cash_ratio": 1.0}}]
    captured_core: dict[str, object] = {}
    saved: list[tuple[list[dict], Path]] = []

    class FakeConnection:
        def close(self) -> None:
            pass

    def fake_run_screening_payload_py(
        strategy: str,
        database: str,
        tickers: list[str] | None,
        return_all: bool,
    ) -> list[dict]:
        captured_core.update(
            {
                "strategy": strategy,
                "database": database,
                "tickers": tickers,
                "return_all": return_all,
            }
        )
        return payload

    def fake_save_screening_payload_json(rows: list[dict], path: Path) -> None:
        saved.append((rows, path))

    fake_core = types.ModuleType("formula_screening._core")
    fake_core.run_screening_payload_py = fake_run_screening_payload_py
    monkeypatch.setitem(sys.modules, "formula_screening._core", fake_core)
    monkeypatch.setattr(cli_module, "STOCKS_DB_PATH", db_path)
    monkeypatch.setattr(cli_module, "_GH_PAGES_JSON", gh_pages_json)
    monkeypatch.setattr(cli_module, "get_connection", lambda _db_path: FakeConnection())
    monkeypatch.setattr(cli_module, "ensure_stooq_prices_fresh", lambda **_kwargs: None)
    monkeypatch.setattr(web_mod, "save_screening_payload_json", fake_save_screening_payload_json)

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
        "database": str(db_path),
        "tickers": ["1301", "7203"],
        "return_all": True,
    }
    assert saved == [(payload, gh_pages_json), (payload, json_path)]
