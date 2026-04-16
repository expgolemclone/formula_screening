"""Tests for bootstrap.ensure_data_available — required_sources + lazy proxy/browser."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import pytest

from formula_screening.db.repository import (
    upsert_financial_item,
    upsert_price,
    upsert_shares_outstanding,
    upsert_stock,
)
from formula_screening.db.schema import _SCHEMA_SQL


class _Counter(TypedDict):
    count: int


_STOOQ_HEADER: str = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"


def _write_stooq_txt(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join([_STOOQ_HEADER, *rows]) + "\n", encoding="utf-8")


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """File-backed sqlite DB registered as the global get_connection target."""
    path: Path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()

    from formula_screening.db import schema as schema_mod

    def _connect() -> sqlite3.Connection:
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(schema_mod, "get_connection", _connect)
    return path


@pytest.fixture()
def stooq_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect worker.DATA_DIR so fetch_prices_stooq reads from tmp_path."""
    from formula_screening import worker as worker_mod

    monkeypatch.setattr(worker_mod, "DATA_DIR", tmp_path)
    directory: Path = tmp_path / "stooq"
    directory.mkdir()
    return directory


def _seed_full_stock(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        upsert_stock(conn, "1301", "極洋", "水産", "プライム")
        upsert_financial_item(conn, "1301", "2024-03", "pl", "revenue", 1_000, "irbank")
        upsert_financial_item(conn, "1301", "2024-03", "bs", "total_assets", 2_000, "irbank_bs")
        upsert_price(conn, "1301", "2024-06-01", 100.0, None)
        upsert_shares_outstanding(conn, "1301", 10_000)
        conn.commit()
    finally:
        conn.close()


def _seed_only_irbank(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        upsert_stock(conn, "1301", "極洋", "水産", "プライム")
        upsert_financial_item(conn, "1301", "2024-03", "pl", "revenue", 1_000, "irbank")
        upsert_financial_item(conn, "1301", "2024-03", "bs", "total_assets", 2_000, "irbank_bs")
        conn.commit()
    finally:
        conn.close()


def _must_not_call(name: str) -> Callable[[], object]:
    def _raise() -> object:
        raise AssertionError(f"{name} must not be called")

    return _raise


class TestEnsureDataAvailableRequiredSources:
    """required_sources filters which missing data triggers auto-fetch."""

    def test_all_required_data_present_returns_without_side_effects(
        self,
        db_path: Path,
        stooq_dir: Path,
    ) -> None:
        # Arrange
        _seed_full_stock(db_path)
        from formula_screening.bootstrap import ensure_data_available

        # Act
        ensure_data_available(
            required_sources=["irbank", "irbank_bs", "prices"],
            get_proxy_pool=_must_not_call("get_proxy_pool"),
            get_browser=_must_not_call("get_browser"),
        )

        # Assert — no exceptions, no callables invoked

    def test_missing_forecast_ignored_when_not_required(
        self,
        db_path: Path,
        stooq_dir: Path,
    ) -> None:
        # Arrange — irbank_forecast empty but strategy does not need it
        _seed_full_stock(db_path)
        from formula_screening.bootstrap import ensure_data_available

        # Act
        ensure_data_available(
            required_sources=["irbank", "irbank_bs", "prices"],
            get_proxy_pool=_must_not_call("get_proxy_pool"),
            get_browser=_must_not_call("get_browser"),
        )

        # Assert — no scraping attempted

    def test_missing_prices_with_local_file_skips_proxy_and_browser(
        self,
        db_path: Path,
        stooq_dir: Path,
    ) -> None:
        # Arrange — only prices missing, local stooq txt available
        _seed_only_irbank(db_path)
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
        _write_stooq_txt(
            stooq_dir / f"{today_str}_d.txt",
            [f"1301.JP,D,{yesterday_str},000000,100,110,90,105,1000,0"],
        )
        from formula_screening.bootstrap import ensure_data_available

        proxy_calls: _Counter = {"count": 0}
        browser_calls: _Counter = {"count": 0}

        def _get_proxy_pool() -> object:
            proxy_calls["count"] += 1
            raise AssertionError("get_proxy_pool must not be called when only prices missing")

        def _get_browser() -> object:
            browser_calls["count"] += 1
            raise AssertionError("get_browser must not be called when local stooq exists")

        # Act
        ensure_data_available(
            required_sources=["irbank", "irbank_bs", "prices"],
            get_proxy_pool=_get_proxy_pool,
            get_browser=_get_browser,
        )

        # Assert
        assert proxy_calls["count"] == 0
        assert browser_calls["count"] == 0
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.row_factory = sqlite3.Row
        try:
            row = verify_conn.execute(
                "SELECT close FROM prices WHERE ticker = ?", ("1301",)
            ).fetchone()
            assert row is not None
            assert row["close"] == 105.0
        finally:
            verify_conn.close()
