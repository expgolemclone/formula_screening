from __future__ import annotations

from pathlib import Path

import pytest

from formula_screening._core import (
    run_screening_payload_py,
    run_screening_payload_with_diagnostics_py,
)
from stock_db.storage.connection import get_connection
from stock_db.storage.financials import upsert_financial_item
from stock_db.storage.prices import (
    get_previous_jpx_business_day,
    upsert_price,
    upsert_shares_outstanding,
)
from stock_db.storage.schema import init_db
from stock_db.storage.stocks import upsert_stock

_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "strategies" / "net_cash_fcf.toml"


def _upsert_items(
    conn,
    ticker: str,
    period: str,
    statement: str,
    items: dict[str, float],
    source: str = "edinet_xbrl",
) -> None:
    for item_name, value in items.items():
        upsert_financial_item(conn, ticker, period, statement, item_name, value, source)


def _insert_screening_stock(
    conn,
    *,
    ticker: str,
    name: str,
    forecast_net_income_current: float,
    fcf_start: float = 1_000.0,
    include_dividend: bool = True,
) -> None:
    upsert_stock(conn, ticker, name, "sector", "market")
    upsert_shares_outstanding(conn, ticker, 1_000)
    upsert_price(conn, ticker, get_previous_jpx_business_day().isoformat(), 10.0, 1_000)

    _upsert_items(
        conn,
        ticker,
        "2025-03",
        "bs",
        {
            "current_assets": 50_000.0,
            "current_liabilities": 5_000.0,
            "non_current_liabilities": 4_000.0,
            "inventories": 1_000.0,
            "investment_securities": 1_000.0,
            "total_assets": 100_000.0,
            "stockholders_equity": 60_000.0,
            "total_equity": 60_000.0,
            "short_term_debt": 1_000.0,
            "long_term_debt": 2_000.0,
            "has_preferred_shares": 1.0,
        },
    )
    _upsert_items(
        conn,
        ticker,
        "2025-03",
        "pl",
        {
            "revenue": 20_000.0,
            "cost_of_revenue": 12_000.0,
            "operating_income": 3_000.0,
            "ordinary_income": 2_500.0,
            "net_income": 2_000.0,
            "eps": 200.0,
        },
    )
    if include_dividend:
        _upsert_items(
            conn,
            ticker,
            "2025-03",
            "dividend",
            {"dps": 1.0, "dividend_payment": -100.0},
        )
    _upsert_items(
        conn,
        ticker,
        "26.3",
        "forecast",
        {
            "net_income_current": forecast_net_income_current,
            "net_income_next": 2_500.0,
            "eps_current": 220.0,
            "eps_next": 240.0,
        },
        source="shikiho",
    )

    for offset, year in enumerate(range(2025, 2015, -1)):
        cf_items = {"free_cf": fcf_start - offset * 10.0}
        if offset == 0:
            cf_items["treasury_stock_purchase"] = -500.0
        _upsert_items(
            conn,
            ticker,
            f"{year}-03",
            "cf",
            cf_items,
        )

    for offset, year in enumerate(range(2024, 2019, -1), start=1):
        _upsert_items(
            conn,
            ticker,
            f"{year}-03",
            "pl",
            {"eps": 200.0 - offset * 20.0},
        )


def test_rust_payload_preserves_python_screening_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STOCK_DB_VAR_DIR", str(tmp_path))
    db_path = tmp_path / "db" / "stocks.db"
    conn = get_connection(db_path)
    try:
        init_db(conn)
        _insert_screening_stock(
            conn,
            ticker="1111",
            name="pass stock",
            forecast_net_income_current=2_000.0,
        )
        _insert_screening_stock(
            conn,
            ticker="2222",
            name="fail stock",
            forecast_net_income_current=400.0,
            fcf_start=400.0,
        )
        conn.commit()
    finally:
        conn.close()

    payload = run_screening_payload_py(
        str(_STRATEGY_PATH),
        ["1111", "2222"],
        False,
    )
    assert [row["code"] for row in payload] == ["1111"]

    row = payload[0]
    assert set(row) == {
        "code",
        "name",
        "price",
        "price_date",
        "metrics",
        "fcf_yield_avg",
        "croic",
        "fcf_cagr",
        "fcf_cagr_r2",
        "fcf_sma_cagr",
        "peg_trailing_5",
        "peg_trailing_5_status",
        "peg_blended_5y_actual_2f",
        "peg_blended_5y_actual_2f_status",
        "has_preferred_shares",
    }
    assert set(row["metrics"]) == {
        "net_cash_ratio",
        "per_actual",
        "per",
        "per_next",
        "pbr",
        "dividend_yield",
        "total_payout_ratio",
        "equity_ratio",
        "market_cap",
    }
    assert row["name"] == "pass stock"
    assert row["price"] == pytest.approx(10.0)
    assert row["price_date"] == get_previous_jpx_business_day().isoformat()
    assert row["metrics"]["market_cap"] == pytest.approx(10_000.0)
    assert row["metrics"]["net_cash_ratio"] == pytest.approx(4.07)
    assert row["metrics"]["per"] == pytest.approx(5.0)
    assert row["metrics"]["per_actual"] == pytest.approx(5.0)
    assert row["metrics"]["per_next"] == pytest.approx(4.0)
    assert row["metrics"]["pbr"] == pytest.approx(1 / 6)
    assert row["metrics"]["dividend_yield"] == pytest.approx(10.0)
    assert row["metrics"]["total_payout_ratio"] == pytest.approx(6.0)
    assert row["metrics"]["equity_ratio"] == pytest.approx(60.0)
    assert row["fcf_yield_avg"] is not None
    assert row["croic"] is not None
    assert row["peg_trailing_5_status"] == "ok"
    assert row["peg_blended_5y_actual_2f_status"] == "ok"
    assert row["peg_trailing_5"] is not None
    assert row["peg_trailing_5_status"] == "ok"
    assert row["peg_blended_5y_actual_2f"] is not None
    assert row["peg_blended_5y_actual_2f_status"] == "ok"
    assert row["has_preferred_shares"] is True

    all_payload = run_screening_payload_py(
        str(_STRATEGY_PATH),
        ["1111", "2222"],
        True,
    )
    assert {row["code"] for row in all_payload} == {"1111", "2222"}


def test_rust_payload_diagnostics_cover_all_screened_stocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STOCK_DB_VAR_DIR", str(tmp_path))
    db_path = tmp_path / "db" / "stocks.db"
    conn = get_connection(db_path)
    try:
        init_db(conn)
        _insert_screening_stock(
            conn,
            ticker="1111",
            name="pass stock",
            forecast_net_income_current=2_000.0,
        )
        _insert_screening_stock(
            conn,
            ticker="2222",
            name="fail stock",
            forecast_net_income_current=400.0,
            fcf_start=400.0,
            include_dividend=False,
        )
        conn.commit()
    finally:
        conn.close()

    result = run_screening_payload_with_diagnostics_py(
        str(_STRATEGY_PATH),
        ["1111", "2222"],
        False,
    )

    assert [row["code"] for row in result["payload"]] == ["1111"]
    assert result["diagnostics"] == [
        {
            "code": "2222",
            "name": "fail stock",
            "missing_fields": ["metrics.dividend_yield"],
        }
    ]


def test_rust_payload_marks_negative_peg_growth_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "db" / "stocks.db"
    conn = get_connection(db_path)
    try:
        init_db(conn)
        _insert_screening_stock(
            conn,
            ticker="3333",
            name="negative peg stock",
            forecast_net_income_current=2_000.0,
        )
        for period, eps in {
            "2025-03": 80.0,
            "2024-03": 90.0,
            "2023-03": 100.0,
            "2022-03": 110.0,
            "2021-03": 120.0,
            "2020-03": 130.0,
        }.items():
            conn.execute(
                """
                UPDATE financial_items
                SET value = ?
                WHERE ticker = '3333'
                  AND period = ?
                  AND statement = 'pl'
                  AND item_name = 'eps'
                """,
                (eps, period),
            )
        conn.execute(
            """
            UPDATE financial_items
            SET value = CASE item_name
                WHEN 'eps_current' THEN 70.0
                WHEN 'eps_next' THEN 60.0
                ELSE value
            END
            WHERE ticker = '3333'
              AND statement = 'forecast'
              AND item_name IN ('eps_current', 'eps_next')
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("STOCK_DB_VAR_DIR", str(tmp_path))
    result = run_screening_payload_with_diagnostics_py(
        str(_STRATEGY_PATH),
        ["3333"],
        False,
    )

    row = result["payload"][0]
    assert row["peg_trailing_5"] is None
    assert row["peg_trailing_5_status"] == "non_positive_growth"
    assert row["peg_blended_5y_actual_2f"] is None
    assert row["peg_blended_5y_actual_2f_status"] == "non_positive_growth"
    assert result["diagnostics"] == [
        {
            "code": "3333",
            "name": "negative peg stock",
            "missing_fields": ["peg_trailing_5", "peg_blended_5y_actual_2f"],
        }
    ]
