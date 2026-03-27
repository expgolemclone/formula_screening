"""Tests for datasources/edinetdb.py."""

import sqlite3
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from formula_screening.datasources.edinetdb import (
    _extract_dividend,
    _extract_items,
    fetch_all_financials,
)
from formula_screening.db.repository import get_financial_dict, upsert_stock
from formula_screening.db.schema import _SCHEMA_SQL


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQL)
    yield c
    c.close()


def _make_financials_df():
    """Create a mock yfinance financials DataFrame."""
    dates = [pd.Timestamp("2024-03-31"), pd.Timestamp("2023-03-31")]
    data = {
        "Total Revenue": [1000.0, 900.0],
        "Operating Income": [200.0, 180.0],
        "Net Income": [150.0, 130.0],
        "Basic EPS": [50.0, 45.0],
    }
    return pd.DataFrame(data, index=dates).T


def test_extract_items_pl():
    df = _make_financials_df()
    field_map = {"Total Revenue": "revenue", "Operating Income": "operating_income"}
    items = _extract_items(df, "pl", "7203", field_map)
    assert len(items) == 4  # 2 fields x 2 periods
    revenues = [i for i in items if i["item_name"] == "revenue"]
    assert revenues[0]["value"] == 1000.0
    assert revenues[0]["period"] == "2024-03"


def test_extract_items_empty():
    assert _extract_items(None, "pl", "7203", {}) == []
    assert _extract_items(pd.DataFrame(), "pl", "7203", {}) == []


def test_extract_dividend():
    ticker_obj = MagicMock()
    ticker_obj.dividends = pd.Series(
        [25.0, 30.0],
        index=[pd.Timestamp("2023-09-28"), pd.Timestamp("2024-03-28")],
    )
    items = _extract_dividend(ticker_obj, "7203")
    assert len(items) == 1  # Both fall in fiscal year ending 2024-03
    assert items[0]["value"] == 55.0
    assert items[0]["period"] == "2024-03"


@patch("formula_screening.datasources.edinetdb._fetch_ticker")
def test_fetch_all_financials(mock_fetch, conn):
    upsert_stock(conn, "7203", "トヨタ", "輸送用機器", "プライム")
    conn.commit()

    mock_fetch.return_value = [
        {"ticker": "7203", "period": "2024-03", "statement": "pl",
         "item_name": "revenue", "value": 1000.0, "source": "yfinance"},
    ]
    total = fetch_all_financials(conn, tickers={"7203"}, years=1)
    assert total == 1
    result = get_financial_dict(conn, "7203")
    assert result["pl"]["revenue"] == 1000.0
