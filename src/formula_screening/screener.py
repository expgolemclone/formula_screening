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

from formula_screening.db.repository import (
    get_all_tickers,
    get_financial_dict,
    get_historical_items,
    get_latest_price_with_shares,
    get_stock_names,
)
from formula_screening.config import MAGIC
from formula_screening.metrics import compute_metrics

logger = logging.getLogger("formula_screening.screener")

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


def _resolve_value(
    source: str | Callable[[dict], float | None],
    stock: dict,
) -> float | None:
    if callable(source):
        return source(stock)
    return stock.get("metrics", {}).get(source)


def _build_screen_fn(
    filters: list[tuple[str | Callable[[dict], float | None], str, float | tuple[float, float]]],
) -> Callable[[dict], bool]:
    def screen(stock: dict) -> bool:
        for source, op, threshold in filters:
            value: float | None = _resolve_value(source, stock)
            if value is None:
                return False
            if op == "between":
                lo: float = threshold[0]  # type: ignore[index]
                hi: float = threshold[1]  # type: ignore[index]
                if not (lo < value < hi):
                    return False
            else:
                cmp: Callable[[float, float], bool] = _OPS[op]
                if not cmp(value, threshold):  # type: ignore[arg-type]
                    return False
        return True

    return screen


def _build_sort_key_fn(
    sort_spec: str | Callable[[dict], float | None],
) -> Callable[[dict], float]:
    def sort_key(stock: dict) -> float:
        value: float | None = _resolve_value(sort_spec, stock)
        return value if value is not None else float("-inf")

    return sort_key


def _build_columns_fn(
    columns_spec: list[tuple[str, str | Callable[[dict], float | None], str]],
) -> Callable[[dict], list[tuple[str, str]]]:
    def columns(stock: dict) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for header, source, fmt in columns_spec:
            value: float | None = _resolve_value(source, stock)
            formatted: str = fmt.format(value) if value is not None else "-"
            result.append((header, formatted))
        return result

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

    filters: list | None = getattr(mod, "FILTERS", None)
    if filters is not None:
        mod.screen = _build_screen_fn(filters)
    elif not (hasattr(mod, "screen") and callable(mod.screen)):
        msg = f"Strategy {path} must define FILTERS or a 'screen(stock) -> bool' function"
        raise ImportError(msg)

    sort_spec: str | Callable | None = getattr(mod, "SORT", None)
    if sort_spec is not None and not hasattr(mod, "sort_key"):
        mod.sort_key = _build_sort_key_fn(sort_spec)

    columns_spec: list | None = getattr(mod, "COLUMNS", None)
    if columns_spec is not None and not hasattr(mod, "columns"):
        mod.columns = _build_columns_fn(columns_spec)

    return mod


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

    return {
        "ticker": ticker,
        "name": name,
        "price": price,
        "shares_outstanding": shares,
        "pl": financials.get("pl", {}),
        "bs": financials.get("bs", {}),
        "cf": financials.get("cf", {}),
        "dividend": financials.get("dividend", {}),
        "ss": financials.get("ss", {}),
        "forecast": financials.get("forecast", {}),
        "metrics": metrics,
        "cf_history": cf_history,
    }


def _screen_chunk(
    tickers: list[str],
    names: dict[str, str],
    screen_fn: Callable[[dict], bool],
    strategy_path: Path,
) -> tuple[list[dict], int]:
    """Screen a chunk of tickers using a thread-local DB connection."""
    from formula_screening.db.schema import get_connection

    conn: sqlite3.Connection = get_connection()
    hits: list[dict] = []
    errors: int = 0
    try:
        for ticker in tickers:
            try:
                stock: dict = build_stock_dict(conn, ticker, names.get(ticker, ""))
                if screen_fn(stock):
                    hits.append(stock)
            except Exception:
                errors += 1
                logger.debug("Error screening %s", ticker, exc_info=True)
    finally:
        conn.close()
    return hits, errors


def run_screening(
    conn: sqlite3.Connection,
    strategy_path: Path,
    *,
    workers: int = 1,
) -> list[dict]:
    """Run a screening strategy against all stocks in the DB.

    Returns:
        List of stock dicts that passed the screen() filter.
    """
    import concurrent.futures

    mod: ModuleType = load_strategy(strategy_path)
    screen_fn: Callable[[dict], bool] = mod.screen

    tickers: list[str] = get_all_tickers(conn)
    logger.info("Screening %d stocks with %s (workers=%d)", len(tickers), strategy_path.name, workers)

    names: dict[str, str] = get_stock_names(conn)

    effective_workers: int = min(workers, len(tickers)) or 1

    if effective_workers == 1:
        all_hits, total_errors = _screen_chunk(tickers, names, screen_fn, strategy_path)
    else:
        chunks: list[list[str]] = [[] for _ in range(effective_workers)]
        for i, ticker in enumerate(tickers):
            chunks[i % effective_workers].append(ticker)

        all_hits = []
        total_errors: int = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures: list[concurrent.futures.Future[tuple[list[dict], int]]] = [
                executor.submit(_screen_chunk, chunk, names, screen_fn, strategy_path)
                for chunk in chunks
            ]
            for future in concurrent.futures.as_completed(futures):
                hits, errors = future.result()
                all_hits.extend(hits)
                total_errors += errors

    logger.info(
        "Screening complete: %d hits / %d total (%d errors)",
        len(all_hits), len(tickers), total_errors,
    )
    return all_hits
