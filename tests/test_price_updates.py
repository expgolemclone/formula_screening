from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import formula_screening.price_updates as price_updates
from stock_db.sources.stooq import StooqDailyPriceUpdateError, StooqPriceUpdateCommandResult
from stock_db.storage.connection import get_connection
from stock_db.storage.prices import upsert_price
from stock_db.storage.schema import init_db


def _init_price_db(db_path: Path, latest_price_date: str | None) -> None:
    conn = get_connection(db_path)
    try:
        init_db(conn)
        if latest_price_date is not None:
            upsert_price(conn, "1234", latest_price_date, 100.0, 1000)
        conn.commit()
    finally:
        conn.close()


def test_ensure_stooq_prices_fresh_skips_update_when_fresh_on_jpx_holiday(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"
    _init_price_db(db_path, "2026-05-01")
    called = False

    def fake_run_stooq_price_update_command(**kwargs: object) -> StooqPriceUpdateCommandResult:
        nonlocal called
        called = True
        raise AssertionError(f"unexpected update: {kwargs}")

    monkeypatch.setattr(price_updates, "run_stooq_price_update_command", fake_run_stooq_price_update_command)

    result = price_updates.ensure_stooq_prices_fresh(
        db_path=db_path,
        today=date(2026, 5, 6),
    )

    assert result is None
    assert called is False


def test_ensure_stooq_prices_fresh_runs_update_when_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"
    _init_price_db(db_path, "2026-05-08")
    captured: dict[str, object] = {}

    def fake_run_stooq_price_update_command(**kwargs: object) -> StooqPriceUpdateCommandResult:
        captured.update(kwargs)
        return StooqPriceUpdateCommandResult(
            stdout="",
            stderr="Imported 1 JP prices for 20260511",
        )

    monkeypatch.setattr(price_updates, "run_stooq_price_update_command", fake_run_stooq_price_update_command)

    result = price_updates.ensure_stooq_prices_fresh(
        db_path=db_path,
        today=date(2026, 5, 11),
    )

    assert result is not None
    assert result.stderr == "Imported 1 JP prices for 20260511"
    assert captured == {}


def test_ensure_stooq_prices_fresh_propagates_update_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stocks.db"
    _init_price_db(db_path, "2026-05-08")

    def fake_run_stooq_price_update_command(**kwargs: object) -> StooqPriceUpdateCommandResult:
        del kwargs
        raise StooqDailyPriceUpdateError("Unauthorized")

    monkeypatch.setattr(price_updates, "run_stooq_price_update_command", fake_run_stooq_price_update_command)

    with pytest.raises(StooqDailyPriceUpdateError, match="Unauthorized"):
        price_updates.ensure_stooq_prices_fresh(
            db_path=db_path,
            today=date(2026, 5, 11),
        )
