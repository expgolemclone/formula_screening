"""Tests for worker orchestration — fetch_prices_stooq lazy browser policy."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

from formula_screening.db.repository import upsert_stock
from formula_screening.db.schema import _SCHEMA_SQL

if TYPE_CHECKING:
    pass


_HEADER: str = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"


def _write_stooq_txt(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join([_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


class _BrowserCall(TypedDict):
    count: int


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a file-backed SQLite DB and patch get_connection to use it.

    File-backed (not ``:memory:``) because each ``get_connection()`` call in the
    production code opens a fresh connection and closes it in a ``finally``
    block. Multiple ``:memory:`` connections would not share state.
    """
    path: Path = tmp_path / "test.db"
    bootstrap_conn = sqlite3.connect(str(path))
    bootstrap_conn.row_factory = sqlite3.Row
    bootstrap_conn.executescript(_SCHEMA_SQL)
    bootstrap_conn.commit()
    bootstrap_conn.close()

    from formula_screening.db import schema as schema_mod

    def _connect() -> sqlite3.Connection:
        conn: sqlite3.Connection = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(schema_mod, "get_connection", _connect)
    return path


@pytest.fixture()
def verify_conn(db_path: Path) -> sqlite3.Connection:
    """Secondary connection for test assertions on the shared DB file."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def stooq_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect worker.DATA_DIR to tmp_path so stooq lookups hit the fixture."""
    from formula_screening import worker as worker_mod

    monkeypatch.setattr(worker_mod, "DATA_DIR", tmp_path)
    directory = tmp_path / "stooq"
    directory.mkdir()
    return directory


def _seed_stock(db_path: Path, ticker: str, name: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        upsert_stock(conn, ticker, name, "水産", "プライム")
        conn.commit()
    finally:
        conn.close()


class TestFetchPricesStooqLazyBrowser:
    """fetch_prices_stooq should only start a browser when a local txt is absent."""

    def test_local_file_present_skips_get_browser(
        self,
        db_path: Path,
        verify_conn: sqlite3.Connection,
        stooq_dir: Path,
    ) -> None:
        # Arrange
        _write_stooq_txt(
            stooq_dir / "20260410_d.txt",
            ["1301.JP,D,20260409,000000,5130,5190,5100,5100,29300,0"],
        )
        _seed_stock(db_path, "1301", "極洋")

        browser_calls: _BrowserCall = {"count": 0}

        def _get_browser() -> object:
            browser_calls["count"] += 1
            raise AssertionError("get_browser should not be invoked when local file exists")

        from formula_screening.worker import fetch_prices_stooq

        # Act
        stats = fetch_prices_stooq(["1301"], get_browser=_get_browser, force=True)

        # Assert
        assert browser_calls["count"] == 0
        assert stats["ok"] == 1
        assert stats["fail"] == 0
        row = verify_conn.execute(
            "SELECT close FROM prices WHERE ticker = ?", ("1301",)
        ).fetchone()
        assert row is not None
        assert row["close"] == 5100.0

    def test_local_file_absent_and_no_get_browser_returns_no_op(
        self,
        db_path: Path,
        verify_conn: sqlite3.Connection,
        stooq_dir: Path,
    ) -> None:
        # Arrange
        _seed_stock(db_path, "1301", "極洋")

        from formula_screening.worker import fetch_prices_stooq

        # Act
        stats = fetch_prices_stooq(["1301"], get_browser=None, force=True)

        # Assert
        assert stats["ok"] == 0
        row = verify_conn.execute("SELECT COUNT(*) AS cnt FROM prices").fetchone()
        assert row["cnt"] == 0

    def test_local_file_absent_invokes_get_browser_exactly_once(
        self,
        db_path: Path,
        stooq_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange
        _seed_stock(db_path, "1301", "極洋")

        def _fake_download(_browser: object, download_dir: str) -> Path:
            return _write_stooq_txt(
                Path(download_dir) / "20260410_d.txt",
                ["1301.JP,D,20260409,000000,5130,5190,5100,5100,29300,0"],
            )

        from formula_screening.scrape import stooq_price as stooq_mod

        monkeypatch.setattr(stooq_mod, "download_daily_txt", _fake_download)

        browser_calls: _BrowserCall = {"count": 0}
        sentinel_browser: object = object()

        def _get_browser() -> object:
            browser_calls["count"] += 1
            return sentinel_browser

        from formula_screening.worker import fetch_prices_stooq

        # Act
        stats = fetch_prices_stooq(["1301"], get_browser=_get_browser, force=True)

        # Assert
        assert browser_calls["count"] == 1
        assert stats["ok"] == 1
