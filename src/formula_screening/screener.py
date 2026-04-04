"""Screening engine: load strategies, build stock dicts, apply filters."""

from __future__ import annotations

import importlib.util
import logging
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


def load_strategy(path: Path) -> ModuleType:
    """Dynamically load a strategy .py file and return the module.

    The module must define a ``screen(stock: dict) -> bool`` function.
    """
    spec = importlib.util.spec_from_file_location("strategy", path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load strategy from {path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"strategy_{path.stem}"] = mod
    spec.loader.exec_module(mod)

    if not hasattr(mod, "screen") or not callable(mod.screen):
        msg = f"Strategy {path} must define a 'screen(stock) -> bool' function"
        raise ImportError(msg)

    return mod


def build_stock_dict(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
) -> dict:
    """Build the nested dict passed to the user's screen() function.

    Fetches cached financials from DB and live price from yfinance.
    """
    financials = get_financial_dict(conn, ticker)
    price_data = get_latest_price_with_shares(conn, ticker)

    price = price_data["price"]
    shares = price_data["shares_outstanding"]

    # Fallback: estimate shares from BS data (株主資本 / BPS)
    if shares is None:
        bs = financials.get("bs", {})
        equity = bs.get("stockholders_equity")
        bps = bs.get("bps")
        if equity and bps and bps > 0:
            shares = int(equity / bps)

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
