from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import formula_screening.cli as cli_module
from stock_db.sources.stooq import StooqDailyPriceUpdateError


def test_cmd_screen_stops_when_stooq_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    strategy_path = tmp_path / "strategy.py"
    strategy_path.write_text("def screen(stock):\n    return True\n", encoding="utf-8")

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
