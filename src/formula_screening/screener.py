"""Screening engine: load strategies, build stock dicts, apply filters."""

from __future__ import annotations

import importlib.util
import logging
import operator
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from formula_screening.config import MAGIC
from stock_db.paths import STOCKS_DB_PATH
from stock_db.storage.connection import get_connection
from stock_db.storage.financials import get_financial_dict, get_historical_items
from stock_db.storage.prices import get_latest_price_with_shares, get_price_at_or_before
from stock_db.storage.stocks import get_all_tickers, get_stock_names
from formula_screening.metrics import compute_metrics
from formula_screening.screen_output import (
    ScreenColumn,
    build_common_link_columns,
    merge_screen_columns,
)

logger = logging.getLogger("formula_screening.screener")

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

MetricValue = int | float
FilterSource = str | Callable[[dict], MetricValue | None]
FilterThreshold = MetricValue | tuple[MetricValue, MetricValue]
ColumnSourceValue = MetricValue | str | None
ColumnSource = str | Callable[[dict], ColumnSourceValue]
ColumnsFn = Callable[[dict], list[ScreenColumn]]


def _resolve_value(
    source: FilterSource,
    stock: dict,
) -> MetricValue | None:
    if callable(source):
        return source(stock)
    return stock["metrics"].get(source)


def _build_screen_fn(
    filters: list[tuple[FilterSource, str, FilterThreshold]],
) -> Callable[[dict], bool]:
    def screen(stock: dict) -> bool:
        for source, op, threshold in filters:
            value: MetricValue | None = _resolve_value(source, stock)
            if value is None:
                return False
            if op == "between":
                if not isinstance(threshold, tuple):
                    msg = f"between filter requires a tuple threshold: {threshold!r}"
                    raise TypeError(msg)
                lo: MetricValue = threshold[0]
                hi: MetricValue = threshold[1]
                if not (lo < value < hi):
                    return False
            else:
                cmp: Callable[[float, float], bool] = _OPS[op]
                if isinstance(threshold, tuple):
                    msg = f"non-between filter requires a scalar threshold: {threshold!r}"
                    raise TypeError(msg)
                if not cmp(value, threshold):
                    return False
        return True

    return screen


def _build_sort_key_fn(
    sort_spec: FilterSource,
) -> Callable[[dict], float]:
    def sort_key(stock: dict) -> float:
        value: MetricValue | None = _resolve_value(sort_spec, stock)
        return value if value is not None else float("-inf")

    return sort_key


def _build_columns_fn(
    columns_spec: list[tuple[str, ColumnSource, str]],
) -> ColumnsFn:
    def columns(stock: dict) -> list[ScreenColumn]:
        result: list[ScreenColumn] = []
        for header, source, fmt in columns_spec:
            value: ColumnSourceValue
            if callable(source):
                value = source(stock)
            else:
                value = stock["metrics"].get(source)
            formatted: str = fmt.format(value) if value is not None else "-"
            result.append((header, formatted))
        return result

    return columns


def _build_combined_columns_fn(base_columns_fn: ColumnsFn | None) -> ColumnsFn:
    def columns(stock: dict) -> list[ScreenColumn]:
        base_columns: list[ScreenColumn] = [] if base_columns_fn is None else base_columns_fn(stock)
        common_columns: list[ScreenColumn] = build_common_link_columns(stock)
        return merge_screen_columns(base_columns, common_columns)

    return columns


def load_strategy(path: Path) -> ModuleType:
    """Dynamically load a strategy .py file and return the module.

    Supports two formats:
    - Declarative: module-level ``FILTERS`` list (and optional ``SORT``, ``COLUMNS``)
    - Function-based: ``screen(stock: dict) -> bool`` function
    """
    spec = importlib.util.spec_from_file_location("strategy", path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load strategy from {path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"strategy_{path.stem}"] = mod
    spec.loader.exec_module(mod)

    filters: list[tuple[FilterSource, str, FilterThreshold]] | None = getattr(mod, "FILTERS", None)
    if filters is not None:
        mod.screen = _build_screen_fn(filters)
    elif not (hasattr(mod, "screen") and callable(mod.screen)):
        msg = f"Strategy {path} must define FILTERS or a 'screen(stock) -> bool' function"
        raise ImportError(msg)

    sort_spec: FilterSource | None = getattr(mod, "SORT", None)
    if sort_spec is not None and not hasattr(mod, "sort_key"):
        mod.sort_key = _build_sort_key_fn(sort_spec)

    base_columns_fn: ColumnsFn | None = None
    columns_attr = getattr(mod, "columns", None)
    if callable(columns_attr):
        base_columns_fn = columns_attr
    else:
        columns_spec: list[tuple[str, ColumnSource, str]] | None = getattr(mod, "COLUMNS", None)
        if columns_spec is not None:
            base_columns_fn = _build_columns_fn(columns_spec)

    mod.columns = _build_combined_columns_fn(base_columns_fn)

    return mod


def screen_single(
    conn: sqlite3.Connection,
    strategy_path: Path,
    ticker: str,
) -> tuple[dict, bool]:
    """Run a strategy against a single ticker.

    Returns (stock_dict, passed).
    """
    mod: ModuleType = load_strategy(strategy_path)
    names: dict[str, str] = get_stock_names(conn)
    stock: dict = build_stock_dict(conn, ticker, names.get(ticker, ""))
    passed: bool = mod.screen(stock)
    return stock, passed


def build_stock_dict(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
) -> dict:
    """Build the nested dict passed to the user's screen() function.

    Fetches cached financials and prices from the DB.
    """
    financials = get_financial_dict(conn, ticker)
    price_data = get_latest_price_with_shares(conn, ticker)

    price = price_data["price"]
    shares = price_data["shares_outstanding"]

    metrics = compute_metrics(financials, price, shares)

    cf_history = get_historical_items(conn, ticker, "cf", n_periods=MAGIC["screening"]["fcf_years"])
    n_pl_periods = max(
        MAGIC["screening"]["peg_trailing_years"] + 1,
        MAGIC["screening"]["peg_blended_actual_years"] + 1,
    )
    pl_history = get_historical_items(conn, ticker, "pl", n_periods=n_pl_periods)

    price_at_period: dict[str, float | None] = {}
    for period, _cf in cf_history:
        date_end = f"{period}-31"
        price_at_period[period] = get_price_at_or_before(conn, ticker, date_end)

    return {
        "ticker": ticker,
        "name": name,
        "price": price,
        "shares_outstanding": shares,
        "pl": financials.get("pl", {}),
        "bs": financials.get("bs", {}),
        "cf": financials.get("cf", {}),
        "dividend": financials.get("dividend", {}),
        "forecast": financials.get("forecast", {}),
        "metrics": metrics,
        "cf_history": cf_history,
        "pl_history": pl_history,
        "price_at_period": price_at_period,
    }


def _screen_chunk(
    tickers: list[str],
    names: dict[str, str],
    screen_fn: Callable[[dict], bool],
    strategy_path: Path,
) -> tuple[list[dict], list[dict], int]:
    """Screen a chunk of tickers using a thread-local DB connection.

    Returns (all_stocks, hits, errors).
    """
    from stock_db.storage.connection import get_connection

    conn: sqlite3.Connection = get_connection(STOCKS_DB_PATH)
    all_stocks: list[dict] = []
    hits: list[dict] = []
    errors: int = 0
    try:
        for ticker in tickers:
            try:
                stock: dict = build_stock_dict(conn, ticker, names.get(ticker, ""))
                all_stocks.append(stock)
                if screen_fn(stock):
                    hits.append(stock)
            except (sqlite3.Error, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                errors += 1
                logger.debug("Error screening %s: %s", ticker, exc, exc_info=True)
    finally:
        conn.close()
    return all_stocks, hits, errors


def run_screening(
    conn: sqlite3.Connection,
    strategy_path: Path,
    *,
    workers: int = 1,
    tickers: list[str] | None = None,
    return_all: bool = False,
) -> list[dict]:
    """Run a screening strategy against all stocks in the DB.

    Returns:
        If return_all is True, all screened stock dicts.
        Otherwise, only stock dicts that passed the screen() filter.
    """
    import concurrent.futures

    mod: ModuleType = load_strategy(strategy_path)
    screen_fn: Callable[[dict], bool] = mod.screen

    if tickers is None:
        tickers = get_all_tickers(conn)
    logger.info("Screening %d stocks with %s (workers=%d)", len(tickers), strategy_path.name, workers)

    names: dict[str, str] = get_stock_names(conn)

    effective_workers: int = min(workers, len(tickers)) or 1

    if effective_workers == 1:
        all_stocks, all_hits, total_errors = _screen_chunk(tickers, names, screen_fn, strategy_path)
    else:
        chunks: list[list[str]] = [[] for _ in range(effective_workers)]
        for i, ticker in enumerate(tickers):
            chunks[i % effective_workers].append(ticker)

        all_stocks = []
        all_hits = []
        total_errors: int = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures: list[concurrent.futures.Future[tuple[list[dict], list[dict], int]]] = [
                executor.submit(_screen_chunk, chunk, names, screen_fn, strategy_path)
                for chunk in chunks
            ]
            for future in concurrent.futures.as_completed(futures):
                stocks, hits, errors = future.result()
                all_stocks.extend(stocks)
                all_hits.extend(hits)
                total_errors += errors

    logger.info(
        "Screening complete: %d hits / %d total (%d errors)",
        len(all_hits), len(tickers), total_errors,
    )
    return all_stocks if return_all else all_hits
