"""Tests for the IR BANK JSON importer."""

import json
import sqlite3

import pytest

from formula_screening.datasources.irbank import import_irbank_json
from formula_screening.db.repository import get_all_tickers, get_financial_dict
from formula_screening.db.schema import _SCHEMA_SQL


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQL)
    yield c
    c.close()


def _write_json(path, meta_type, meta_codes, items):
    data = {"meta": {"type": meta_type, "item": {"code": meta_codes}}, "item": items}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def irbank_dir(tmp_path):
    """Create a minimal IR BANK data directory with one year."""
    year_dir = tmp_path / "0025"
    year_dir.mkdir()

    _write_json(
        year_dir / "fy-profit-and-loss.json",
        "業績",
        ["年度", "売上高", "営業利益", "経常利益", "純利益", "EPS", "ROE", "ROA"],
        {
            "7203": ["2025/03", 1000000, 150000, 140000, 100000, 50.5, 12.5, 5.0],
            "6861": ["2025/03", 800000, 300000, 290000, 200000, 100.0, "-", 8.0],
        },
    )
    _write_json(
        year_dir / "fy-balance-sheet.json",
        "財務",
        ["年度", "総資産", "純資産", "株主資本", "利益剰余金", "短期借入金", "長期借入金", "BPS", "自己資本比率"],
        {
            "7203": ["2025/03", 5000000, 2000000, 1800000, 1500000, 100000, 200000, 900.0, 36.0],
        },
    )
    _write_json(
        year_dir / "fy-cash-flow-statement.json",
        "CF",
        ["年度", "営業CF", "投資CF", "財務CF", "設備投資", "現金同等物", "営業CFマージン"],
        {
            "7203": ["2025/03", 200000, -80000, -50000, -70000, 300000, 20.0],
        },
    )
    _write_json(
        year_dir / "fy-stock-dividend.json",
        "配当",
        ["年度", "一株配当", "剰余金の配当", "自社株買い", "配当性向", "総還元性向", "純資産配当率"],
        {
            "7203": ["2025/03", 25.0, 50000, 30000, 49.5, 60.0, 2.5],
        },
    )
    return tmp_path


def test_import_registers_tickers(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    tickers = get_all_tickers(conn)
    assert "6861" in tickers
    assert "7203" in tickers


def test_import_pl_values(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    fd = get_financial_dict(conn, "7203", period="2025-03")
    assert fd["pl"]["revenue"] == 1000000.0
    assert fd["pl"]["operating_income"] == 150000.0
    assert fd["pl"]["roe"] == 12.5


def test_import_dash_becomes_none(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    fd = get_financial_dict(conn, "6861", period="2025-03")
    assert fd["pl"]["roe"] is None
    assert fd["pl"]["roa"] == 8.0


def test_import_bs_values(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    fd = get_financial_dict(conn, "7203", period="2025-03")
    assert fd["bs"]["total_assets"] == 5000000.0
    assert fd["bs"]["equity_ratio"] == 36.0


def test_import_cf_values(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    fd = get_financial_dict(conn, "7203", period="2025-03")
    assert fd["cf"]["operating_cf"] == 200000.0
    assert fd["cf"]["investing_cf"] == -80000.0


def test_import_dividend_values(conn, irbank_dir):
    import_irbank_json(conn, irbank_dir)
    fd = get_financial_dict(conn, "7203", period="2025-03")
    assert fd["dividend"]["dps"] == 25.0
    assert fd["dividend"]["payout_ratio"] == 49.5


def test_import_returns_item_count(conn, irbank_dir):
    total = import_irbank_json(conn, irbank_dir)
    assert total > 0


def test_import_period_format(conn, irbank_dir):
    """Period format should be normalized from '2025/03' to '2025-03'."""
    import_irbank_json(conn, irbank_dir)
    row = conn.execute(
        "SELECT period FROM financial_items WHERE ticker='7203' LIMIT 1"
    ).fetchone()
    assert row["period"] == "2025-03"


def test_import_years_filter(conn, irbank_dir):
    """The years parameter limits how many year directories are imported."""
    # Create a second year directory
    year_dir = irbank_dir / "0024"
    year_dir.mkdir()
    _write_json(
        year_dir / "fy-profit-and-loss.json",
        "業績",
        ["年度", "売上高", "営業利益", "経常利益", "純利益", "EPS", "ROE", "ROA"],
        {"9999": ["2024/03", 500, 50, 40, 30, 10.0, 5.0, 2.0]},
    )

    import_irbank_json(conn, irbank_dir, years=1)

    # Only the latest year (0025) should be imported
    tickers = get_all_tickers(conn)
    assert "9999" not in tickers
    assert "7203" in tickers
