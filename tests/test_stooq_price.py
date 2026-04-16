"""Tests for scrape.stooq_price — parse_daily_txt and find_latest_daily_txt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from formula_screening.scrape.stooq_price import (
    find_latest_daily_txt,
    parse_daily_txt,
)

HEADER: str = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"


def _write_txt(path: Path, rows: list[str]) -> Path:
    lines = [HEADER] + rows
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestParseDailyTxt:

    def test_extracts_close_for_matching_jp_tickers(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [
            "1301.JP,D,20260409,000000,5130,5190,5100,5100,29300,0",
        ])

        # Act
        result = parse_daily_txt(txt, tickers={"1301"})

        # Assert
        assert "1301" in result
        assert result["1301"]["price"] == 5100.0
        assert result["1301"]["date"] == "2026-04-09"

    def test_ignores_tickers_not_in_filter_set(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [
            "9984.JP,D,20260409,000000,3822,3823,3640,3775,45064100,0",
        ])

        # Act
        result = parse_daily_txt(txt, tickers={"7203"})

        # Assert
        assert result == {}

    def test_handles_multiple_jp_tickers(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [
            "1301.JP,D,20260409,000000,5130,5190,5100,5100,29300,0",
            "9984.JP,D,20260409,000000,3822,3823,3640,3775,45064100,0",
        ])

        # Act
        result = parse_daily_txt(txt, tickers={"1301", "9984"})

        # Assert
        assert result["1301"]["price"] == 5100.0
        assert result["9984"]["price"] == 3775.0

    def test_skips_non_jp_tickers(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [
            "^NKX,D,20260410,000000,56273.41,57008.61,56257.14,56924.11,0,0",
            "AAPL.US,D,20260410,000000,200,210,195,205,1000000,0",
            "1301.JP,D,20260409,000000,5130,5190,5100,5100,29300,0",
        ])

        # Act
        result = parse_daily_txt(txt, tickers={"1301"})

        # Assert
        assert len(result) == 1
        assert "1301" in result

    def test_handles_5_digit_ticker(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [
            "13010.JP,D,20260409,000000,100,110,95,105,1000,0",
        ])

        # Act
        result = parse_daily_txt(txt, tickers={"13010"})

        # Assert
        assert "13010" in result
        assert result["13010"]["price"] == 105.0

    def test_empty_file_returns_nothing(self, tmp_path: Path) -> None:
        # Arrange
        txt = _write_txt(tmp_path / "20260410_d.txt", [])

        # Act
        result = parse_daily_txt(txt, tickers={"1301"})

        # Assert
        assert result == {}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _days_ago_str(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")


class TestFindLatestDailyTxt:

    def test_finds_latest_fresh_file(self, tmp_path: Path) -> None:
        # Arrange
        (tmp_path / f"{_days_ago_str(2)}_d.txt").write_text("old")
        (tmp_path / f"{_today_str()}_d.txt").write_text("new")
        (tmp_path / f"{_days_ago_str(1)}_d.txt").write_text("mid")

        # Act
        result = find_latest_daily_txt(tmp_path, max_age_days=1)

        # Assert
        assert result is not None
        assert result.name == f"{_today_str()}_d.txt"

    def test_returns_none_when_no_files(self, tmp_path: Path) -> None:
        # Act
        result = find_latest_daily_txt(tmp_path)

        # Assert
        assert result is None

    def test_ignores_non_matching_files(self, tmp_path: Path) -> None:
        # Arrange
        (tmp_path / "d_jp_ms.zip").write_text("zip")
        (tmp_path / "error.txt").write_text("err")
        (tmp_path / f"{_today_str()}_d.txt").write_text("data")

        # Act
        result = find_latest_daily_txt(tmp_path, max_age_days=1)

        # Assert
        assert result is not None
        assert result.name == f"{_today_str()}_d.txt"

    def test_returns_none_when_all_files_stale(self, tmp_path: Path) -> None:
        # Arrange — files older than max_age_days
        (tmp_path / f"{_days_ago_str(5)}_d.txt").write_text("old")
        (tmp_path / f"{_days_ago_str(3)}_d.txt").write_text("mid")

        # Act
        result = find_latest_daily_txt(tmp_path, max_age_days=1)

        # Assert
        assert result is None

    def test_returns_file_within_threshold(self, tmp_path: Path) -> None:
        # Arrange — file from yesterday, max_age_days=2 so it should be accepted
        yesterday = _days_ago_str(1)
        (tmp_path / f"{yesterday}_d.txt").write_text("yesterday")

        # Act
        result = find_latest_daily_txt(tmp_path, max_age_days=2)

        # Assert
        assert result is not None
        assert result.name == f"{yesterday}_d.txt"
