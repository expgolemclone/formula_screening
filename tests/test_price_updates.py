from __future__ import annotations

from datetime import date

import pytest

import formula_screening.price_updates as price_updates
from stock_db.api import PriceRefreshCommandResult, PriceRefreshError


def test_ensure_prices_fresh_runs_stock_db_refresh_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stock_db_ensure_prices_fresh() -> PriceRefreshCommandResult:
        return PriceRefreshCommandResult(
            stdout="",
            stderr="Refreshed stock prices: target_date=2026-05-08, yahoo=1 ok",
        )

    monkeypatch.setattr(
        price_updates,
        "_stock_db_ensure_prices_fresh",
        fake_stock_db_ensure_prices_fresh,
    )

    result = price_updates.ensure_prices_fresh()

    assert result is not None
    assert result.stderr == "Refreshed stock prices: target_date=2026-05-08, yahoo=1 ok"


def test_ensure_stooq_prices_fresh_keeps_backward_compatible_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_ensure_prices_fresh(**kwargs: object) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr(price_updates, "ensure_prices_fresh", fake_ensure_prices_fresh)

    result = price_updates.ensure_stooq_prices_fresh(
        today=date(2026, 5, 11),
    )

    assert result is None
    assert captured == {"today": date(2026, 5, 11)}


def test_ensure_prices_fresh_propagates_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stock_db_ensure_prices_fresh() -> PriceRefreshCommandResult:
        raise PriceRefreshError("Yahoo failed")

    monkeypatch.setattr(
        price_updates,
        "_stock_db_ensure_prices_fresh",
        fake_stock_db_ensure_prices_fresh,
    )

    with pytest.raises(PriceRefreshError, match="Yahoo failed"):
        price_updates.ensure_prices_fresh()
