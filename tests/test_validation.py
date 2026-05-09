from __future__ import annotations

import sqlite3

from stock_db.storage.schema import init_db

from formula_screening.validation import (
    build_net_cash_snapshot,
    load_latest_bs,
    select_validation_targets,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_select_validation_targets_orders_by_market_cap() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO stocks (
                ticker, name, sector, market, shares_outstanding, securities_report_url, updated_at
            ) VALUES
                ('1111', 'Alpha', '', '', 100, 'https://example.com/a.pdf', '2026-01-01'),
                ('2222', 'Beta', '', '', 50, 'https://example.com/b.pdf', '2026-01-01'),
                ('3333', 'Gamma', '', '', NULL, 'https://example.com/c.pdf', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO prices (ticker, date, close, volume, updated_at) VALUES
                ('1111', '2026-04-20', 100.0, 1, '2026-04-20T00:00:00+00:00'),
                ('2222', '2026-04-20', 300.0, 1, '2026-04-20T00:00:00+00:00'),
                ('3333', '2026-04-20', 999.0, 1, '2026-04-20T00:00:00+00:00')
            """
        )
        conn.commit()

        targets = select_validation_targets(conn, 2)

        assert [target.ticker for target in targets] == ["2222", "1111"]
    finally:
        conn.close()


def test_load_latest_bs_returns_xbrl_data() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO financial_items (
                ticker, period, statement, item_name, value, source, updated_at
            ) VALUES
                ('8888', '2025-03', 'bs', 'current_assets', 38675872000, 'xbrl_bs', '2026-01-01'),
                ('8888', '2025-03', 'bs', 'inventories', 32974467000, 'xbrl_bs', '2026-01-01')
            """
        )
        conn.commit()

        period, bs, status = load_latest_bs(conn, "8888")
        assert period == "2025-03"
        assert status is None
        assert bs["inventories"] == 32_974_467_000
    finally:
        conn.close()


def test_load_latest_bs_returns_missing_when_xbrl_absent() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO financial_items (
                ticker, period, statement, item_name, value, source, updated_at
            ) VALUES
                ('5280', '2025-03', 'bs', 'current_assets', 38675872000, 'other_source', '2026-01-01'),
                ('5280', '2025-03', 'bs', 'inventories', 32983204000, 'other_source', '2026-01-01')
            """
        )
        conn.commit()

        period, bs, status = load_latest_bs(conn, "5280")

        assert period is None
        assert status == "scrape_missing"
        assert bs == {}
    finally:
        conn.close()


def test_load_latest_bs_propagates_status_rows() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO financial_items (
                ticker, period, statement, item_name, value, source, updated_at
            ) VALUES
                ('7000', '2025-03', '_status', 'blocked', NULL, 'xbrl_bs', '2026-01-01')
            """
        )
        conn.commit()

        period, bs, status = load_latest_bs(conn, "7000")

        assert period is None
        assert status == "scrape_blocked"
        assert bs == {}
    finally:
        conn.close()


def test_build_net_cash_snapshot_computes_ratio() -> None:
    snapshot = build_net_cash_snapshot(
        "2025-03",
        {
            "current_assets": 38_675_872_000.0,
            "inventories": 32_974_467_000.0,
            "investment_securities": 2_985_654_000.0,
            "current_liabilities": 15_158_894_000.0,
            "non_current_liabilities": 1_468_637_000.0,
        },
        price=2567.0,
        shares_outstanding=8_030_248,
    )

    assert snapshot.market_cap is not None
    assert snapshot.net_cash is not None
    assert snapshot.net_cash_ratio is not None
