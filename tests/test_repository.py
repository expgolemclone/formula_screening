"""Tests for db.repository."""

from formula_screening.db.repository import (
    get_all_tickers,
    get_cached_periods,
    get_financial_dict,
    upsert_financial_item,
    upsert_financial_items_bulk,
    upsert_stock,
)


def test_upsert_stock_and_get_tickers(conn):
    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    upsert_stock(conn, "6861", "キーエンス", "電気機器", "プライム")
    conn.commit()

    tickers = get_all_tickers(conn)
    assert tickers == ["6861", "7203"]


def test_upsert_stock_updates_on_conflict(conn):
    upsert_stock(conn, "7203", "Old", "Old", "Old")
    upsert_stock(conn, "7203", "New", "New", "New")
    conn.commit()

    row = conn.execute("SELECT name FROM stocks WHERE ticker='7203'").fetchone()
    assert row["name"] == "New"


def test_upsert_financial_item_and_get_dict(conn):
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 48036704, "edinetdb")
    upsert_financial_item(conn, "7203", "2024-03", "bs", "total_assets", 93601350, "edinetdb")
    conn.commit()

    result = get_financial_dict(conn, "7203")
    assert result["pl"]["revenue"] == 48036704.0
    assert result["bs"]["total_assets"] == 93601350.0


def test_get_financial_dict_latest_period(conn):
    upsert_financial_item(conn, "7203", "2023-03", "pl", "revenue", 100, "edinetdb")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 200, "edinetdb")
    conn.commit()

    result = get_financial_dict(conn, "7203")
    assert result["pl"]["revenue"] == 200.0


def test_get_financial_dict_specific_period(conn):
    upsert_financial_item(conn, "7203", "2023-03", "pl", "revenue", 100, "edinetdb")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 200, "edinetdb")
    conn.commit()

    result = get_financial_dict(conn, "7203", period="2023-03")
    assert result["pl"]["revenue"] == 100.0


def test_bulk_upsert(conn):
    rows = [
        {"ticker": "7203", "period": "2024-03", "statement": "pl", "item_name": "revenue", "value": 100, "source": "edinetdb"},
        {"ticker": "7203", "period": "2024-03", "statement": "pl", "item_name": "net_income", "value": 50, "source": "edinetdb"},
    ]
    upsert_financial_items_bulk(conn, rows)
    conn.commit()

    result = get_financial_dict(conn, "7203")
    assert result["pl"]["revenue"] == 100.0
    assert result["pl"]["net_income"] == 50.0


def test_get_cached_periods(conn):
    upsert_financial_item(conn, "7203", "2023-03", "pl", "revenue", 100, "edinetdb")
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 200, "edinetdb")
    conn.commit()

    periods = get_cached_periods(conn, "7203", "pl")
    assert periods == {"2023-03", "2024-03"}


def test_get_financial_dict_empty(conn):
    result = get_financial_dict(conn, "9999")
    assert result == {}


def test_get_financial_dict_forecast_uses_latest_period(conn):
    """When multiple forecast periods exist, only the latest should be returned."""
    upsert_financial_item(conn, "7203", "2024-03", "pl", "revenue", 100, "irbank")
    # Older forecast
    upsert_financial_item(conn, "7203", "2025-03", "forecast", "basic_eps", 55.0, "irbank_forecast")
    # Newer forecast
    upsert_financial_item(conn, "7203", "2026-03", "forecast", "basic_eps", 60.0, "irbank_forecast")
    conn.commit()

    result = get_financial_dict(conn, "7203")
    assert result["forecast"]["basic_eps"] == 60.0
