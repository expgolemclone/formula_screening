"""Tests for scraper hash change detection and cache invalidation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from formula_screening.cache_invalidation import (
    compute_hashes,
    detect_changes,
    invalidate_cache,
    load_saved_hashes,
    save_hashes,
)
from formula_screening.db.repository import upsert_financial_item, upsert_price


class TestComputeHashes:
    def test_returns_sha256_for_tracked_files(self, tmp_path: Path) -> None:
        (tmp_path / "irbank.py").write_text("# v1")
        (tmp_path / "yfinance_price.py").write_text("# price v1")

        hashes: dict[str, str] = compute_hashes(tmp_path)

        assert "irbank.py" in hashes
        assert "yfinance_price.py" in hashes
        assert len(hashes["irbank.py"]) == 64

    def test_ignores_untracked_files(self, tmp_path: Path) -> None:
        (tmp_path / "unrelated.py").write_text("# not tracked")

        hashes: dict[str, str] = compute_hashes(tmp_path)

        assert "unrelated.py" not in hashes


class TestDetectChanges:
    def test_new_file(self) -> None:
        old: dict[str, str] = {}
        new: dict[str, str] = {"irbank.py": "abc123"}

        result: list[str] = detect_changes(old, new)

        assert result == ["irbank.py"]

    def test_modified_file(self) -> None:
        old: dict[str, str] = {"irbank.py": "abc123"}
        new: dict[str, str] = {"irbank.py": "def456"}

        result: list[str] = detect_changes(old, new)

        assert result == ["irbank.py"]

    def test_no_change(self) -> None:
        hashes: dict[str, str] = {"irbank.py": "abc123", "yfinance_price.py": "def456"}

        result: list[str] = detect_changes(hashes, hashes)

        assert result == []

    def test_common_module_change(self) -> None:
        old: dict[str, str] = {"irbank_common.py": "v1"}
        new: dict[str, str] = {"irbank_common.py": "v2"}

        result: list[str] = detect_changes(old, new)

        assert result == ["irbank_common.py"]


class TestSaveAndLoadHashes:
    def test_roundtrip(self, tmp_path: Path) -> None:
        hashes: dict[str, str] = {"irbank.py": "abc", "irbank_bs.py": "def"}
        path: Path = tmp_path / "hashes.json"

        save_hashes(hashes, path)
        loaded: dict[str, str] = load_saved_hashes(path)

        assert loaded == hashes

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result: dict[str, str] = load_saved_hashes(tmp_path / "nonexistent.json")

        assert result == {}


class TestInvalidateCache:
    def test_deletes_financial_items_by_source(self, conn: sqlite3.Connection) -> None:
        upsert_financial_item(conn, "7203", "2024-03", "bs", "total_assets", 1000, "irbank_bs")
        upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 500, "irbank")
        conn.commit()

        result: dict[str, int] = invalidate_cache(["irbank_bs.py"], conn=conn)

        assert result["financial_items[source=irbank_bs]"] > 0
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM financial_items WHERE source = 'irbank'"
        ).fetchone()
        assert row["cnt"] == 1

    def test_deletes_prices(self, conn: sqlite3.Connection) -> None:
        upsert_price(conn, "7203", "2024-06-01", 2500.0, None, shares_outstanding=1000)
        conn.commit()

        result: dict[str, int] = invalidate_cache(["yfinance_price.py"], conn=conn)

        assert result["prices"] > 0

    def test_common_module_invalidates_both(self, conn: sqlite3.Connection) -> None:
        upsert_financial_item(conn, "7203", "2024-03", "bs", "total_assets", 1000, "irbank_bs")
        upsert_financial_item(conn, "7203", "2025-03", "forecast", "basic_eps", 50, "irbank_forecast")
        conn.commit()

        result: dict[str, int] = invalidate_cache(["irbank_common.py"], conn=conn)

        assert "financial_items[source=irbank_bs]" in result
        assert "financial_items[source=irbank_forecast]" in result

    def test_empty_changes_returns_empty(self, conn: sqlite3.Connection) -> None:
        result: dict[str, int] = invalidate_cache([], conn=conn)

        assert result == {}
