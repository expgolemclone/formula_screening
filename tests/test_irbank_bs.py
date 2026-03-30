"""Tests for the IRBank BS page scraper."""

from __future__ import annotations

import sqlite3

import pytest

from formula_screening.datasources.irbank_bs import build_bs_rows, parse_bs_charts
from formula_screening.db.schema import _SCHEMA_SQL


# --- Mock HTML with embedded gGm chart data -----------------------------------

_MOCK_HTML_JP_GAAP = """
<html><head><script>
google.charts.load('current', {packages: ['corechart']});
google.charts.setOnLoadCallback(function(){
gGm([["year","投資等","無形固定資産","有形固定資産","その他流動資産","たな卸資産","現金等","売上債権","その他資産"],["2024年12月",{v:70553660000,f:"705億5366万"},{v:2958014000,f:"29億5801万"},{v:100000000000,f:"1000億"},{v:5000000000,f:"50億"},{v:8000000000,f:"80億"},{v:13108300000,f:"131億830万"},{v:20000000000,f:"200億"},{v:3000000000,f:"30億"}],["2025年12月",{v:80000000000,f:"800億"},{v:3000000000,f:"30億"},{v:110000000000,f:"1100億"},{v:6000000000,f:"60億"},{v:9000000000,f:"90億"},{v:15000000000,f:"150億"},{v:22000000000,f:"220億"},{v:4000000000,f:"40億"}]],"debit",32,1,[]);
gGm([["year","株主資本","その他純資産","固定負債","その他流動負債","仕入債務"],["2024年12月",{v:82936400000,f:"829億3640万"},{v:10981800000,f:"109億8180万"},{v:207610000000,f:"2076億1000万"},{v:30000000000,f:"300億"},{v:10000000000,f:"100億"}],["2025年12月",{v:90000000000,f:"900億"},{v:12000000000,f:"120億"},{v:220000000000,f:"2200億"},{v:35000000000,f:"350億"},{v:12000000000,f:"120億"}]],"credit",32,1,[]);
gGm([["年","固定資産","流動資産","純資産","固定負債","流動負債"],["2024年12月 借方",{v:173511674000,f:"1735億1167万"},{v:46108300000,f:"461億830万"},null,null,null],["2024年12月 貸方",null,null,{v:93918200000,f:"939億1820万"},{v:207610000000,f:"2076億1000万"},{v:40000000000,f:"400億"}],["2025年12月 借方",{v:193000000000,f:"1930億"},{v:52000000000,f:"520億"},null,null,null],["2025年12月 貸方",null,null,{v:102000000000,f:"1020億"},{v:220000000000,f:"2200億"},{v:47000000000,f:"470億"}]],"percentage",42,1,[]);
});
</script></head><body></body></html>
"""

_MOCK_HTML_IFRS = """
<html><head><script>
google.charts.setOnLoadCallback(function(){
gGm([["year","投資等","有形固定資産","その他流動資産","たな卸資産","売上債権","現金等","無形固定資産","その他資産"],["2025年3月",{v:10980420000000,f:"10兆9804億"},{v:11411153000000,f:"11兆4111億"},{v:11829173000000,f:"11兆8291億"},{v:2888028000000,f:"2兆8880億"},{v:2958742000000,f:"2兆9587億"},{v:5100857000000,f:"5兆1008億"},{v:1108634000000,f:"1兆1086億"},{v:26970553000000,f:"26兆9705億"}]],"debit",32,1,[]);
gGm([["year","株主資本","その他純資産","固定負債","その他流動負債","仕入債務"],["2025年3月",{v:23404547000000,f:"23兆4045億"},{v:883782000000,f:"8837億8200万"},{v:16518344000000,f:"16兆5183億"},{v:17414527000000,f:"17兆4145億"},{v:4045939000000,f:"4兆459億"}]],"credit",32,1,[]);
gGm([["年","固定資産","流動資産","純資産","固定負債","流動負債"],["2025年3月 借方",{v:50470760000000,f:"50兆4707億"},{v:22776800000000,f:"22兆7768億"},null,null,null],["2025年3月 貸方",null,null,{v:24288329000000,f:"24兆2883億"},{v:16518344000000,f:"16兆5183億"},{v:21460466000000,f:"21兆4604億"}]],"percentage",42,1,[]);
});
</script></head><body></body></html>
"""


# --- parse_bs_charts tests ----------------------------------------------------


def test_parse_debit_columns_jp_gaap():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    assert "debit" in charts
    items = charts["debit"]
    names = {it["item_name"] for it in items}
    assert "investment_securities" in names
    assert "tangible_fixed_assets" in names
    assert "cash_and_deposits" in names
    assert "inventories" in names
    assert "trade_receivables" in names


def test_parse_credit_columns_jp_gaap():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    assert "credit" in charts
    items = charts["credit"]
    names = {it["item_name"] for it in items}
    assert "stockholders_equity" in names
    assert "other_equity" in names
    assert "non_current_liabilities" in names
    assert "trade_payables" in names


def test_parse_percentage_chart():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    assert "percentage" in charts
    items = charts["percentage"]
    names = {it["item_name"] for it in items}
    assert "current_assets" in names
    assert "fixed_assets" in names
    assert "current_liabilities" in names


def test_parse_debit_values():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    debit = charts["debit"]
    inv = [it for it in debit if it["item_name"] == "investment_securities" and it["period"] == "2024-12"]
    assert len(inv) == 1
    assert inv[0]["value"] == 70553660000


def test_parse_credit_values():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    credit = charts["credit"]
    eq = [it for it in credit if it["item_name"] == "stockholders_equity" and it["period"] == "2024-12"]
    assert len(eq) == 1
    assert eq[0]["value"] == 82936400000


def test_parse_percentage_with_nulls():
    """Percentage chart has 借方/貸方 rows with nulls — only non-null values kept."""
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    pct = charts["percentage"]
    ca = [it for it in pct if it["item_name"] == "current_assets" and it["period"] == "2024-12"]
    assert len(ca) == 1
    assert ca[0]["value"] == 46108300000


def test_parse_multiple_periods():
    charts = parse_bs_charts(_MOCK_HTML_JP_GAAP)
    debit = charts["debit"]
    periods = {it["period"] for it in debit}
    assert "2024-12" in periods
    assert "2025-12" in periods


def test_parse_ifrs_company():
    charts = parse_bs_charts(_MOCK_HTML_IFRS)
    debit = charts["debit"]
    names = {it["item_name"] for it in debit}
    assert "investment_securities" in names
    assert "intangible_fixed_assets" in names


def test_parse_period_format():
    """Periods should be normalised to YYYY-MM format."""
    charts = parse_bs_charts(_MOCK_HTML_IFRS)
    debit = charts["debit"]
    assert all(it["period"] == "2025-03" for it in debit)


# --- build_bs_rows tests ------------------------------------------------------


def test_build_bs_rows_dedup():
    """Percentage chart items should take priority over debit/credit."""
    rows = build_bs_rows("3003", _MOCK_HTML_JP_GAAP)
    ca_rows = [r for r in rows if r["item_name"] == "current_assets"]
    # current_assets comes from percentage chart; should not duplicate
    periods = [r["period"] for r in ca_rows]
    assert len(periods) == len(set(periods))


def test_build_bs_rows_years_filter():
    rows = build_bs_rows("3003", _MOCK_HTML_JP_GAAP, years=1)
    periods = {r["period"] for r in rows}
    assert periods == {"2025-12"}


def test_build_bs_rows_metadata():
    rows = build_bs_rows("7203", _MOCK_HTML_IFRS)
    assert all(r["ticker"] == "7203" for r in rows)
    assert all(r["statement"] == "bs" for r in rows)
    assert all(r["source"] == "irbank_bs" for r in rows)


# --- net_cash metrics tests ---------------------------------------------------


def test_net_cash_full_formula():
    """When detailed BS is available, use 清原式: current_assets - inventories + investment_securities*0.7 - liabilities."""
    from formula_screening.metrics import compute_metrics

    financials = {
        "pl": {"net_income": 100},
        "bs": {
            "current_assets": 500,
            "inventories": 80,
            "investment_securities": 200,
            "current_liabilities": 300,
            "non_current_liabilities": 100,
            "total_assets": 1000,
            "total_equity": 600,
        },
        "cf": {"cash_equivalents": 50},
    }
    m = compute_metrics(financials, price=None, shares_outstanding=None)
    # 500 - 80 + 200*0.7 - (300+100) = 420 + 140 - 400 = 160
    assert m["net_cash"] == 160.0


def test_net_cash_fallback():
    """Without detailed BS, fall back to cash_equivalents - total_liabilities."""
    from formula_screening.metrics import compute_metrics

    financials = {
        "pl": {},
        "bs": {"total_assets": 1000, "total_equity": 600},
        "cf": {"cash_equivalents": 50},
    }
    m = compute_metrics(financials, price=None, shares_outstanding=None)
    # 50 - (1000 - 600) = 50 - 400 = -350
    assert m["net_cash"] == -350.0


# --- DB integration tests ----------------------------------------------------


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQL)
    yield c
    c.close()
