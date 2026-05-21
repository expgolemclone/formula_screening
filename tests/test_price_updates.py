from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import formula_screening.price_updates as price_updates
from stock_db.sources.price_refresh import PriceRefreshCommandResult, PriceRefreshError


def test_ensure_prices_fresh_runs_stock_db_refresh_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"
    captured: dict[str, object] = {}

    def fake_run_price_refresh_command(**kwargs: object) -> PriceRefreshCommandResult:
        captured.update(kwargs)
        return PriceRefreshCommandResult(
            stdout="",
            stderr="Refreshed stock prices: target_date=2026-05-08, yahoo=1 ok",
        )

    monkeypatch.setattr(price_updates, "run_price_refresh_command", fake_run_price_refresh_command)

    result = price_updates.ensure_prices_fresh(db_path=db_path)

    assert result is not None
    assert result.stderr == "Refreshed stock prices: target_date=2026-05-08, yahoo=1 ok"
    assert captured == {"db_path": db_path, "if_needed": True}


def test_ensure_stooq_prices_fresh_keeps_backward_compatible_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"
    captured: dict[str, object] = {}

    def fake_ensure_prices_fresh(**kwargs: object) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr(price_updates, "ensure_prices_fresh", fake_ensure_prices_fresh)

    result = price_updates.ensure_stooq_prices_fresh(
        db_path=db_path,
        today=date(2026, 5, 11),
    )

    assert result is None
    assert captured == {"db_path": db_path, "today": date(2026, 5, 11)}


def test_ensure_prices_fresh_propagates_refresh_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"

    def fake_run_price_refresh_command(**kwargs: object) -> PriceRefreshCommandResult:
        del kwargs
        raise PriceRefreshError("Yahoo failed")

    monkeypatch.setattr(price_updates, "run_price_refresh_command", fake_run_price_refresh_command)

    with pytest.raises(PriceRefreshError, match="Yahoo failed"):
        price_updates.ensure_prices_fresh(db_path=db_path)
