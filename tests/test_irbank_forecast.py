"""Tests for the IRBank forecast parser."""

from __future__ import annotations

import pytest

from formula_screening.scrape.irbank_forecast import (
    build_forecast_rows,
    parse_forecast_table,
    parse_jp_number,
)


# --- parse_jp_number tests ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("287億", 28_700_000_000),
        ("19.6億", 1_960_000_000),
        ("3億7214万", 372_140_000),
        ("7214万", 72_140_000),
        ("−0.49億", -49_000_000),
        ("-5億", -500_000_000),
        ("55.82", 55.82),
        ("26.71円", 26.71),
        ("7.19%", 7.19),
        ("1,234億", 123_400_000_000),
        # Missing / unparseable
        ("—", None),
        ("−", None),
        ("-", None),
        ("赤字", None),
        ("", None),
    ],
)
def test_parse_jp_number(text: str, expected: float | None) -> None:
    result = parse_jp_number(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# --- HTML table parser tests -------------------------------------------------

_MOCK_RESULTS_HTML = """
<html><body>
<h2>会社業績</h2>
<table>
<tr><td>年度</td><td>売上</td><td>営利</td><td>経常</td><td>当期利益</td><td>包括</td><td>EPS</td><td>ROE</td><td>ROA</td><td>営利率</td><td>原価率</td><td>販管費率</td></tr>
<tr><td>2024/03</td><td>285億2797万</td><td>15億5119万</td><td>15億8075万</td><td>8億3546万</td><td>8億6034万</td><td>26.71円</td><td>3.52%</td><td>2.28%</td><td>5.44%</td><td>84.25%</td><td>10.31%</td></tr>
<tr><td>2025/03予</td><td>287億</td><td>19.6億</td><td>20.2億</td><td>17.6億</td><td>—</td><td>55.82</td><td>7.19%</td><td>5.16%</td><td>6.83%</td><td>—</td><td>—</td></tr>
<tr><td>2026/03予</td><td>300億</td><td>21億</td><td>22億</td><td>18億</td><td>—</td><td>60.00</td><td>7.50%</td><td>5.50%</td><td>7.00%</td><td>—</td><td>—</td></tr>
</table>
</body></html>
"""


def test_parse_forecast_table_extracts_forecast_rows() -> None:
    items = parse_forecast_table(_MOCK_RESULTS_HTML)
    periods = {it["period"] for it in items}
    assert "2025-03" in periods
    assert "2026-03" in periods
    # Actual row (2024/03) should be excluded
    assert "2024-03" not in periods


def test_parse_forecast_table_eps() -> None:
    items = parse_forecast_table(_MOCK_RESULTS_HTML)
    eps_items = [it for it in items if it["item_name"] == "basic_eps"]
    eps_by_period = {it["period"]: it["value"] for it in eps_items}
    assert eps_by_period["2025-03"] == pytest.approx(55.82)
    assert eps_by_period["2026-03"] == pytest.approx(60.00)


def test_parse_forecast_table_monetary() -> None:
    items = parse_forecast_table(_MOCK_RESULTS_HTML)
    revenue = [it for it in items if it["item_name"] == "revenue" and it["period"] == "2025-03"]
    assert len(revenue) == 1
    assert revenue[0]["value"] == pytest.approx(28_700_000_000)


def test_parse_forecast_table_ratios() -> None:
    items = parse_forecast_table(_MOCK_RESULTS_HTML)
    roe = [it for it in items if it["item_name"] == "roe" and it["period"] == "2025-03"]
    assert len(roe) == 1
    assert roe[0]["value"] == pytest.approx(7.19)


def test_parse_forecast_table_missing_values_skipped() -> None:
    items = parse_forecast_table(_MOCK_RESULTS_HTML)
    # "包括" is "—" in the forecast row → should not appear
    comprehensive = [it for it in items if it["item_name"] == "comprehensive_income" and it["period"] == "2025-03"]
    assert comprehensive == []


def test_parse_forecast_table_no_table() -> None:
    items = parse_forecast_table("<html><body><h2>Unrelated</h2></body></html>")
    assert items == []


# --- build_forecast_rows tests -----------------------------------------------


def test_build_forecast_rows_metadata() -> None:
    rows = build_forecast_rows("5282", _MOCK_RESULTS_HTML)
    assert all(r["ticker"] == "5282" for r in rows)
    assert all(r["statement"] == "forecast" for r in rows)
    assert all(r["source"] == "irbank_forecast" for r in rows)


# --- metrics integration: forecast EPS preferred for PER ---------------------


def test_per_uses_forecast_net_income() -> None:
    from formula_screening.metrics import compute_metrics

    financials: dict[str, dict[str, float]] = {
        "pl": {"net_income": 267},
        "forecast": {"net_income": 558},
        "bs": {},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)
    # PER should use forecast net_income: 10000 / 558
    assert m["per"] == pytest.approx(10000.0 / 558)


def test_per_actual_from_pl_net_income() -> None:
    from formula_screening.metrics import compute_metrics

    financials: dict[str, dict[str, float]] = {
        "pl": {"net_income": 267},
        "bs": {},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] is None
    assert m["per_actual"] == pytest.approx(10000.0 / 267)


def test_per_forecast_net_income_zero() -> None:
    from formula_screening.metrics import compute_metrics

    financials: dict[str, dict[str, float]] = {
        "pl": {"net_income": 500},
        "forecast": {"net_income": 0.0},
        "bs": {},
    }
    m: dict[str, float | None] = compute_metrics(financials, price=1000.0, shares_outstanding=10)

    assert m["per"] is None
    assert m["per_actual"] == pytest.approx(10000.0 / 500)
