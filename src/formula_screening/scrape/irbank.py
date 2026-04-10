"""Import IR BANK JSON files into the screening database."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from formula_screening.db.repository import upsert_financial_items_bulk, upsert_stock

logger = logging.getLogger("formula_screening.irbank")

# Maps (json_filename, meta field index) → (statement, item_name).
# Index 0 is always 年度 (period) and is handled separately.
_PL_MAPPING: list[tuple[int, str, str]] = [
    (1, "pl", "revenue"),
    (2, "pl", "operating_income"),
    (3, "pl", "ordinary_income"),
    (4, "pl", "net_income"),
    (5, "pl", "basic_eps"),
    (6, "pl", "roe"),
    (7, "pl", "roa"),
]

_BS_MAPPING: list[tuple[int, str, str]] = [
    (1, "bs", "total_assets"),
    (2, "bs", "total_equity"),
    (3, "bs", "stockholders_equity"),
    (4, "bs", "retained_earnings"),
    (5, "bs", "short_term_debt"),
    (6, "bs", "long_term_debt"),
    (7, "bs", "bps"),
    (8, "bs", "equity_ratio"),
]

_CF_MAPPING: list[tuple[int, str, str]] = [
    (1, "cf", "operating_cf"),
    (2, "cf", "investing_cf"),
    (3, "cf", "financing_cf"),
    (4, "cf", "capex"),
    (5, "cf", "cash_equivalents"),
    (6, "cf", "operating_cf_margin"),
]

_DIVIDEND_MAPPING: list[tuple[int, str, str]] = [
    (1, "dividend", "dps"),
    (2, "dividend", "dividend_payment"),
    (3, "dividend", "buyback"),
    (4, "dividend", "payout_ratio"),
    (5, "dividend", "total_return_ratio"),
    (6, "dividend", "doe"),
]

_FILE_MAPPINGS: dict[str, list[tuple[int, str, str]]] = {
    "fy-profit-and-loss.json": _PL_MAPPING,
    "fy-balance-sheet.json": _BS_MAPPING,
    "fy-cash-flow-statement.json": _CF_MAPPING,
    "fy-stock-dividend.json": _DIVIDEND_MAPPING,
}

# Quarterly (cumulative) mappings.
# Each file contains one metric with values [年度, 1Q, 2Q, 3Q, 4Q].
# We store as statement="qy", item_name="{metric}_{quarter}".
_QY_ITEM_NAMES: dict[str, str] = {
    "qy-net-sales.json": "revenue",
    "qy-operating-income.json": "operating_income",
    "qy-ordinary-income.json": "ordinary_income",
    "qy-profit-loss.json": "net_income",
}
_QY_QUARTERS = ["1q", "2q", "3q", "4q"]


def _parse_value(raw: object) -> float | None:
    """Convert a raw JSON value to float, treating '-' and non-numeric as None."""
    if raw is None or raw == "-" or raw == "":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _normalize_period(raw_period: str) -> str:
    """Convert IR BANK period format to DB format: '2025/03' → '2025-03'."""
    return raw_period.replace("/", "-")


def _import_json_file(
    conn: sqlite3.Connection,
    path: Path,
    mapping: list[tuple[int, str, str]],
) -> tuple[int, set[str]]:
    """Import a single IR BANK JSON file.

    Returns:
        (number of financial items inserted, set of ticker codes found)
    """
    data = json.loads(path.read_bytes())
    items = data.get("item", {})
    tickers_found: set[str] = set()
    rows: list[dict] = []

    for ticker, values in items.items():
        tickers_found.add(ticker)
        if not isinstance(values, list) or len(values) < 1:
            continue

        period = _normalize_period(str(values[0]))

        for idx, statement, item_name in mapping:
            if idx >= len(values):
                continue
            value = _parse_value(values[idx])
            rows.append({
                "ticker": ticker,
                "period": period,
                "statement": statement,
                "item_name": item_name,
                "value": value,
                "source": "irbank",
            })

    if rows:
        upsert_financial_items_bulk(conn, rows)

    return len(rows), tickers_found


def _import_quarterly_file(
    conn: sqlite3.Connection,
    path: Path,
    base_item_name: str,
) -> tuple[int, set[str]]:
    """Import a single quarterly cumulative JSON file.

    Returns:
        (number of financial items inserted, set of ticker codes found)
    """
    data = json.loads(path.read_bytes())
    items = data.get("item", {})
    tickers_found: set[str] = set()
    rows: list[dict] = []

    for ticker, values in items.items():
        tickers_found.add(ticker)
        if not isinstance(values, list) or len(values) < 2:
            continue

        period = _normalize_period(str(values[0]))

        for qi, quarter in enumerate(_QY_QUARTERS):
            idx = qi + 1  # values[0] is 年度, 1Q=values[1], ...
            if idx >= len(values):
                continue
            value = _parse_value(values[idx])
            rows.append({
                "ticker": ticker,
                "period": period,
                "statement": "qy",
                "item_name": f"{base_item_name}_{quarter}",
                "value": value,
                "source": "irbank",
            })

    if rows:
        upsert_financial_items_bulk(conn, rows)

    return len(rows), tickers_found


def import_irbank_json(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    years: int | None = None,
) -> int:
    """Import IR BANK JSON data into the database.

    Scans year-code subdirectories under *data_dir* and imports all
    four JSON files per year.  Also imports quarterly cumulative data
    from the ``quarterly/`` subdirectory if present.

    Tickers found in the JSON are automatically registered in the
    ``stocks`` table.

    Args:
        conn: Database connection.
        data_dir: Root directory containing year-code subdirectories.
        years: If set, only import the most recent N years.

    Returns:
        Total number of financial items imported.
    """
    year_dirs = sorted(
        [d for d in data_dir.iterdir() if d.is_dir() and d.name != "quarterly"],
        key=lambda d: d.name,
    )
    if years is not None:
        year_dirs = year_dirs[-years:]

    all_tickers: set[str] = set()
    total_items = 0

    for year_dir in year_dirs:
        logger.info("Importing %s", year_dir.name)
        for filename, mapping in _FILE_MAPPINGS.items():
            path = year_dir / filename
            if not path.exists():
                logger.warning("Missing %s in %s", filename, year_dir.name)
                continue
            count, tickers = _import_json_file(conn, path, mapping)
            all_tickers.update(tickers)
            total_items += count

    # Import quarterly cumulative data
    qy_dir = data_dir / "quarterly"
    if qy_dir.is_dir():
        logger.info("Importing quarterly data")
        for filename, base_item_name in _QY_ITEM_NAMES.items():
            path = qy_dir / filename
            if not path.exists():
                logger.warning("Missing %s in quarterly/", filename)
                continue
            count, tickers = _import_quarterly_file(conn, path, base_item_name)
            all_tickers.update(tickers)
            total_items += count

    # Register all discovered tickers in the stocks table
    for ticker in sorted(all_tickers):
        upsert_stock(conn, ticker, name="", sector="", market="")
    conn.commit()

    logger.info(
        "Import complete: %d items, %d tickers",
        total_items, len(all_tickers),
    )
    return total_items
