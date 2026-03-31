"""Tests for the screening engine."""

from pathlib import Path

import pytest

from formula_screening.db.repository import (
    upsert_financial_item,
    upsert_price,
    upsert_stock,
)
from formula_screening.screener import load_strategy


def test_load_strategy_value(tmp_path):
    strategy = tmp_path / "test_strat.py"
    strategy.write_text("def screen(stock):\n    return True\n")
    mod = load_strategy(strategy)
    assert mod.screen({}) is True


def test_load_strategy_missing_screen(tmp_path):
    strategy = tmp_path / "bad.py"
    strategy.write_text("x = 1\n")
    with pytest.raises(ImportError, match="screen"):
        load_strategy(strategy)


def test_load_example_strategies():
    """Verify bundled example strategies load without error."""
    examples_dir = Path(__file__).resolve().parent.parent / "strategies" / "examples"
    for f in examples_dir.glob("*.py"):
        mod = load_strategy(f)
        assert callable(mod.screen)


def test_build_stock_dict(conn):
    from formula_screening.screener import build_stock_dict

    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 48036704, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "basic_eps", 359.56, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "bs", "total_equity", 36878914, "irbank")
    upsert_price(conn, "7203", "2024-06-01", 2500.0, None, shares_outstanding=13048929774)
    conn.commit()

    stock = build_stock_dict(conn, "7203", "トヨタ")

    assert stock["ticker"] == "7203"
    assert stock["price"] == 2500.0
    assert stock["pl"]["revenue"] == 48036704.0
    assert stock["metrics"]["per"] is not None
    assert stock["metrics"]["per"] == pytest.approx(2500.0 / 359.56, rel=0.01)


def test_build_stock_dict_no_price(conn):
    """Screening works even without cached price data."""
    from formula_screening.screener import build_stock_dict

    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 1000, "irbank")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "operating_income", 200, "irbank")
    conn.commit()

    stock = build_stock_dict(conn, "7203", "トヨタ")

    assert stock["price"] is None
    assert stock["metrics"]["per"] is None
    assert stock["metrics"]["operating_margin"] == pytest.approx(20.0)
