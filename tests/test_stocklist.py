"""Tests for datasources/stocklist.py."""

import sqlite3
from pathlib import Path

import pytest

from formula_screening.datasources.stocklist import (
    fetch_edinetdb_companies,
    load_manual_stocklist,
)
from formula_screening.db.repository import get_all_tickers
from formula_screening.db.schema import _SCHEMA_SQL


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQL)
    yield c
    c.close()


def test_load_manual_stocklist(conn, tmp_path):
    f = tmp_path / "stocks.txt"
    f.write_text("# comment\n7203,トヨタ自動車\n6861,キーエンス\n", encoding="utf-8")
    count = load_manual_stocklist(conn, f)
    assert count == 2
    assert get_all_tickers(conn) == ["6861", "7203"]


def test_load_manual_stocklist_ticker_only(conn, tmp_path):
    f = tmp_path / "stocks.txt"
    f.write_text("7203\n6861\n", encoding="utf-8")
    count = load_manual_stocklist(conn, f)
    assert count == 2


def test_load_manual_stocklist_skips_blank_and_comments(conn, tmp_path):
    f = tmp_path / "stocks.txt"
    f.write_text("# header\n\n7203\n  \n# another comment\n", encoding="utf-8")
    count = load_manual_stocklist(conn, f)
    assert count == 1


def test_fetch_edinetdb_companies_not_implemented(conn):
    with pytest.raises(NotImplementedError):
        fetch_edinetdb_companies(conn)
