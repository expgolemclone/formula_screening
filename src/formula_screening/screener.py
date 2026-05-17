"""Screening engine: load TOML strategies, build stock dicts, apply filters."""

from __future__ import annotations

import logging
import operator
import sqlite3
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from formula_screening.config import MAGIC
from formula_screening.indicators import croic, fcf_yield_avg, peg_blended_2f, peg_trailing
from formula_screening.preferred_shares import preferred_share_label
from stock_db.paths import STOCKS_DB_PATH
from stock_db.storage.connection import get_connection
from stock_db.storage.financials import get_financial_dict, get_historical_items
from stock_db.storage.prices import get_latest_price_with_shares
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
FilterSource = str
FilterThreshold = MetricValue | tuple[MetricValue, MetricValue]
ColumnSourceValue = MetricValue | str | None
ColumnSource = str
ColumnsFn = Callable[[dict], list[ScreenColumn]]


def _peg_trailing_5(stock: dict) -> float | None:
    return peg_trailing(stock, MAGIC["screening"]["peg_trailing_years"])


def _peg_blended_5y_actual_2f(stock: dict) -> float | None:
    return peg_blended_2f(stock, MAGIC["screening"]["peg_blended_actual_years"])


_DERIVED_SOURCES: dict[str, Callable[[dict], ColumnSourceValue]] = {
    "fcf_yield_avg": fcf_yield_avg,
    "croic": croic,
    "peg_trailing_5": _peg_trailing_5,
    "peg_blended_5y_actual_2f": _peg_blended_5y_actual_2f,
    "preferred_share_label": preferred_share_label,
}


@dataclass(frozen=True, slots=True)
class Strategy:
    required_sources: tuple[str, ...]
    filters: tuple[tuple[FilterSource, str, FilterThreshold], ...]
    sort: FilterSource | None
    column_specs: tuple[tuple[str, ColumnSource, str], ...]

    def screen(self, stock: dict) -> bool:
        return _screen(self.filters, stock)

    def sort_key(self, stock: dict) -> float:
        if self.sort is None:
            return float("-inf")
        value = _resolve_numeric_value(self.sort, stock)
        return value if value is not None else float("-inf")

    def columns(self, stock: dict) -> list[ScreenColumn]:
        base_columns: list[ScreenColumn] = _build_columns(self.column_specs, stock)
        common_columns: list[ScreenColumn] = build_common_link_columns(stock)
        return merge_screen_columns(base_columns, common_columns)


def _resolve_value(
    source: FilterSource,
    stock: dict,
) -> ColumnSourceValue:
    resolver = _DERIVED_SOURCES.get(source)
    if resolver is not None:
        return resolver(stock)
    return stock["metrics"].get(source)


def _resolve_numeric_value(
    source: FilterSource,
    stock: dict,
) -> MetricValue | None:
    value = _resolve_value(source, stock)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        msg = f"Strategy source {source!r} must resolve to a numeric value"
        raise TypeError(msg)
    return value


def _screen(
    filters: tuple[tuple[FilterSource, str, FilterThreshold], ...],
    stock: dict,
) -> bool:
    for source, op, threshold in filters:
        value: MetricValue | None = _resolve_numeric_value(source, stock)
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


def _build_columns(
    columns_spec: tuple[tuple[str, ColumnSource, str], ...],
    stock: dict,
) -> list[ScreenColumn]:
    result: list[ScreenColumn] = []
    for header, source, fmt in columns_spec:
        value = _resolve_value(source, stock)
        formatted: str = fmt.format(value) if value is not None else "-"
        result.append((header, formatted))
    return result


def _load_filter(raw_filter: dict) -> tuple[FilterSource, str, FilterThreshold]:
    source = _require_source(raw_filter["source"])
    op = raw_filter["operator"]
    if op not in {*_OPS, "between"}:
        msg = f"Unsupported strategy operator: {op!r}"
        raise ValueError(msg)

    threshold = raw_filter["threshold"]
    if op == "between":
        if not (
            isinstance(threshold, list)
            and len(threshold) == 2
            and all(isinstance(value, (int, float)) for value in threshold)
        ):
            msg = f"between filter requires two numeric thresholds: {threshold!r}"
            raise TypeError(msg)
        return source, op, (threshold[0], threshold[1])

    if not isinstance(threshold, (int, float)):
        msg = f"non-between filter requires a numeric threshold: {threshold!r}"
        raise TypeError(msg)
    return source, op, threshold


def _load_column(raw_column: dict) -> tuple[str, ColumnSource, str]:
    header = raw_column["header"]
    source = _require_source(raw_column["source"])
    fmt = raw_column["format"]
    if not all(isinstance(value, str) for value in (header, fmt)):
        msg = f"Strategy column values must be strings: {raw_column!r}"
        raise TypeError(msg)
    return header, source, fmt


def _require_source(source: object) -> str:
    if not isinstance(source, str):
        msg = f"Strategy source must be a string: {source!r}"
        raise TypeError(msg)
    if source not in _DERIVED_SOURCES and source not in _KNOWN_METRIC_SOURCES:
        msg = f"Unknown strategy source: {source!r}"
        raise ValueError(msg)
    return source


_KNOWN_METRIC_SOURCES: frozenset[str] = frozenset(
    {
        "market_cap",
        "per",
        "per_next",
        "per_actual",
        "pbr",
        "dividend_yield",
        "gross_margin",
        "operating_margin",
        "ordinary_margin",
        "net_income_margin",
        "roe",
        "roa",
        "equity_ratio",
        "debt_equity_ratio",
        "operating_cf_margin",
        "free_cf",
        "free_cf_ratio",
        "total_liabilities",
        "interest_bearing_debt",
        "net_cash",
        "net_cash_ratio",
    }
)


def load_strategy(path: Path) -> Strategy:
    """Load a TOML strategy definition."""

    if path.suffix != ".toml":
        msg = f"Strategy files must be TOML: {path}"
        raise ValueError(msg)

    with path.open("rb") as f:
        raw = tomllib.load(f)

    filters_raw = raw.get("filters")
    if not isinstance(filters_raw, list) or not filters_raw:
        msg = f"Strategy {path} must define at least one [[filters]] entry"
        raise ValueError(msg)

    required_sources_raw = raw.get("required_sources", [])
    if not isinstance(required_sources_raw, list) or not all(
        isinstance(value, str) for value in required_sources_raw
    ):
        msg = f"Strategy required_sources must be a list of strings: {required_sources_raw!r}"
        raise TypeError(msg)

    sort_raw = raw.get("sort")
    sort = None if sort_raw is None else _require_source(sort_raw)

    columns_raw = raw.get("columns", [])
    if not isinstance(columns_raw, list):
        msg = f"Strategy columns must be a list: {columns_raw!r}"
        raise TypeError(msg)

    return Strategy(
        required_sources=tuple(required_sources_raw),
        filters=tuple(_load_filter(raw_filter) for raw_filter in filters_raw),
        sort=sort,
        column_specs=tuple(_load_column(raw_column) for raw_column in columns_raw),
    )


def screen_single(
    conn: sqlite3.Connection,
    strategy_path: Path,
    ticker: str,
) -> tuple[dict, bool]:
    """Run a strategy against a single ticker.

    Returns (stock_dict, passed).
    """
    mod: Strategy = load_strategy(strategy_path)
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

    mod: Strategy = load_strategy(strategy_path)
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
