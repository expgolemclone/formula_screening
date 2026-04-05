"""Tests for the screening engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from formula_screening.db.repository import (
    upsert_financial_item,
    upsert_price,
    upsert_stock,
)
from formula_screening.screener import load_strategy


def test_load_strategy_value(tmp_path: Path) -> None:
    strategy: Path = tmp_path / "test_strat.py"
    strategy.write_text("def screen(stock):\n    return True\n")
    mod = load_strategy(strategy)
    assert mod.screen({}) is True


def test_load_strategy_missing_screen(tmp_path: Path) -> None:
    strategy: Path = tmp_path / "bad.py"
    strategy.write_text("x = 1\n")
    with pytest.raises(ImportError, match="FILTERS.*screen"):
        load_strategy(strategy)


def test_load_bundled_strategies() -> None:
    """Verify bundled strategies load without error."""
    strategies_dir: Path = Path(__file__).resolve().parent.parent / "strategies"
    files: list[Path] = list(strategies_dir.glob("*.py"))
    assert files, "No strategy files found in strategies/"
    for f in files:
        mod = load_strategy(f)
        assert callable(mod.screen)


class TestDeclarativeFilters:
    """Tests for declarative FILTERS / SORT / COLUMNS strategy format."""

    def test_filters_basic(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "basic.py"
        strategy.write_text(
            'FILTERS = [\n'
            '    ("net_cash_ratio", ">", 1.0),\n'
            '    ("per", "<", 10),\n'
            ']\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"net_cash_ratio": 1.5, "per": 5}}) is True
        assert mod.screen({"metrics": {"net_cash_ratio": 0.5, "per": 5}}) is False

    def test_filters_between(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "between.py"
        strategy.write_text(
            'FILTERS = [("per", "between", (0, 10))]\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"per": 5}}) is True
        assert mod.screen({"metrics": {"per": 0}}) is False
        assert mod.screen({"metrics": {"per": 10}}) is False
        assert mod.screen({"metrics": {"per": -1}}) is False

    def test_filters_none_value_fails(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "none_val.py"
        strategy.write_text(
            'FILTERS = [("per", ">", 0)]\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"per": None}}) is False
        assert mod.screen({"metrics": {}}) is False

    def test_filters_callable_source(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "callable.py"
        strategy.write_text(
            'def my_indicator(stock):\n'
            '    return stock.get("metrics", {}).get("per", 0) * 2\n'
            '\n'
            'FILTERS = [(my_indicator, ">", 10)]\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"per": 10}}) is True
        assert mod.screen({"metrics": {"per": 3}}) is False

    def test_filters_gte_lte(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "gte_lte.py"
        strategy.write_text(
            'FILTERS = [\n'
            '    ("a", ">=", 5),\n'
            '    ("b", "<=", 10),\n'
            ']\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"a": 5, "b": 10}}) is True
        assert mod.screen({"metrics": {"a": 4, "b": 10}}) is False
        assert mod.screen({"metrics": {"a": 5, "b": 11}}) is False

    def test_sort_from_string(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "sort_str.py"
        strategy.write_text(
            'FILTERS = [("per", ">", 0)]\n'
            'SORT = "per"\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert callable(mod.sort_key)
        assert mod.sort_key({"metrics": {"per": 5}}) == 5
        assert mod.sort_key({"metrics": {}}) == float("-inf")

    def test_sort_from_callable(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "sort_fn.py"
        strategy.write_text(
            'def my_score(stock):\n'
            '    return stock.get("metrics", {}).get("per", 0) + 1\n'
            '\n'
            'FILTERS = [("per", ">", 0)]\n'
            'SORT = my_score\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.sort_key({"metrics": {"per": 5}}) == 6

    def test_columns_from_spec(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "cols.py"
        strategy.write_text(
            'def my_val(stock):\n'
            '    return 0.1234\n'
            '\n'
            'FILTERS = [("per", ">", 0)]\n'
            'COLUMNS = [("MyCol", my_val, "{:.2%}")]\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        result: list[tuple[str, str]] = mod.columns({"metrics": {"per": 5}})
        assert result == [("MyCol", "12.34%")]

    def test_columns_none_value_shows_dash(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "cols_none.py"
        strategy.write_text(
            'def returns_none(stock):\n'
            '    return None\n'
            '\n'
            'FILTERS = [("per", ">", 0)]\n'
            'COLUMNS = [("X", returns_none, "{:.2f}")]\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        result: list[tuple[str, str]] = mod.columns({"metrics": {"per": 5}})
        assert result == [("X", "-")]

    def test_no_filters_and_no_screen_raises(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "empty.py"
        strategy.write_text("x = 1\n")

        # Act & Assert
        with pytest.raises(ImportError, match="FILTERS.*screen"):
            load_strategy(strategy)

    def test_filters_takes_priority_over_screen(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "both.py"
        strategy.write_text(
            'FILTERS = [("per", ">", 0)]\n'
            '\n'
            'def screen(stock):\n'
            '    return False\n'
        )

        # Act
        mod = load_strategy(strategy)

        # Assert
        assert mod.screen({"metrics": {"per": 5}}) is True


def test_build_stock_dict(conn: sqlite3.Connection) -> None:
    from formula_screening.screener import build_stock_dict

    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 48036704, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "basic_eps", 359.56, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "bs", "total_equity", 36878914, "irbank")
    upsert_price(conn, "7203", "2024-06-01", 2500.0, None, shares_outstanding=13048929774)
    conn.commit()

    stock: dict = build_stock_dict(conn, "7203", "トヨタ")

    assert stock["ticker"] == "7203"
    assert stock["price"] == 2500.0
    assert stock["pl"]["revenue"] == 48036704.0
    assert stock["metrics"]["per"] is not None
    assert stock["metrics"]["per"] == pytest.approx(2500.0 / 359.56, rel=0.01)


def test_build_stock_dict_cf_history(conn: sqlite3.Connection) -> None:
    """cf_history contains historical CF data newest-first."""
    from formula_screening.screener import build_stock_dict

    upsert_stock(conn, "9999", "テスト", "情報通信", "プライム")
    for period, op_cf, inv_cf in [
        ("2020-03", 100, -30),
        ("2021-03", 120, -40),
        ("2022-03", 150, -50),
    ]:
        upsert_financial_item(conn, "9999", period, "cf", "operating_cf", op_cf, "irbank")
        upsert_financial_item(conn, "9999", period, "cf", "investing_cf", inv_cf, "irbank")
    upsert_financial_item(conn, "9999", "2022-03", "pl", "revenue", 1000, "irbank")
    conn.commit()

    stock: dict = build_stock_dict(conn, "9999", "テスト")

    assert "cf_history" in stock
    history: list[tuple[str, dict]] = stock["cf_history"]
    assert len(history) == 3
    assert history[0][0] == "2022-03"
    assert history[0][1]["operating_cf"] == 150
    assert history[2][0] == "2020-03"


def test_build_stock_dict_no_price(conn: sqlite3.Connection) -> None:
    """Screening works even without cached price data."""
    from formula_screening.screener import build_stock_dict

    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 1000, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "operating_income", 200, "irbank")
    conn.commit()

    stock: dict = build_stock_dict(conn, "7203", "トヨタ")

    assert stock["price"] is None
    assert stock["metrics"]["per"] is None
    assert stock["metrics"]["operating_margin"] == pytest.approx(20.0)
