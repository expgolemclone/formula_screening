"""Tests for worker orchestration — fetch_prices_stooq lazy browser policy."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

from formula_screening.db.repository import upsert_stock
from formula_screening.db.schema import _SCHEMA_SQL
from formula_screening.stealth import ProxyPool

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


class TestScrapeSharesWorker:
    def test_waits_between_every_ticker_even_on_failures(
        self,
        db_path: Path,
        verify_conn: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange
        _seed_stock(db_path, "1111", "銘柄A")
        _seed_stock(db_path, "2222", "銘柄B")
        _seed_stock(db_path, "3333", "銘柄C")

        html_by_ticker: dict[str, str | None] = {
            "1111": None,
            "2222": "<html><body>no shares</body></html>",
            "3333": "<html><body>shares</body></html>",
        }
        row_by_ticker: dict[str, dict[str, int] | None] = {
            "2222": None,
            "3333": {"ticker": "3333", "shares_outstanding": 123_456},
        }
        delay_calls: list[tuple[float, float]] = []

        from formula_screening.scrape import kabutan_shares as shares_mod
        from formula_screening import stealth as stealth_mod
        from formula_screening.worker import scrape_shares_worker

        def _fake_fetch_kabutan_html(ticker: str, pool: ProxyPool) -> str | None:
            del pool
            return html_by_ticker[ticker]

        def _fake_build_shares_row(
            ticker: str,
            html: str,
        ) -> dict[str, int] | None:
            del html
            return row_by_ticker.get(ticker)

        def _fake_random_delay(min_s: float, max_s: float) -> None:
            delay_calls.append((min_s, max_s))

        monkeypatch.setattr(shares_mod, "fetch_kabutan_html", _fake_fetch_kabutan_html)
        monkeypatch.setattr(shares_mod, "build_shares_row", _fake_build_shares_row)
        monkeypatch.setattr(stealth_mod, "random_delay", _fake_random_delay)

        stats: dict[str, int] = {"ok": 0, "skip": 0, "fail": 0}
        stats_lock = threading.Lock()
        counter = [0]

        # Act
        scrape_shares_worker(
            ["1111", "2222", "3333"],
            ProxyPool([], direct=True),
            interval=1.0,
            force=False,
            stats=stats,
            stats_lock=stats_lock,
            total=3,
            counter=counter,
        )

        # Assert
        assert stats == {"ok": 1, "skip": 0, "fail": 2}
        assert delay_calls == [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
        row = verify_conn.execute(
            "SELECT shares_outstanding FROM stocks WHERE ticker = ?",
            ("3333",),
        ).fetchone()
        assert row is not None
        assert row["shares_outstanding"] == 123_456
