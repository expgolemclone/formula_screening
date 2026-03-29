"""Screening engine: load strategies, build stock dicts, apply filters."""

from __future__ import annotations

import importlib.util
import logging
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

from formula_screening.db.repository import (
    get_all_tickers,
    get_financial_dict,
    get_latest_price_with_shares,
)
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
    sys.modules["strategy"] = mod
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
        "metrics": metrics,
    }


def run_screening(
    conn: sqlite3.Connection,
    strategy_path: Path,
) -> list[dict]:
    """Run a screening strategy against all stocks in the DB.

    Returns:
        List of stock dicts that passed the screen() filter.
    """
    mod = load_strategy(strategy_path)
    screen_fn = mod.screen

    tickers = get_all_tickers(conn)
    logger.info("Screening %d stocks with %s", len(tickers), strategy_path.name)

    # Pre-fetch stock names
    names: dict[str, str] = {}
    for row in conn.execute("SELECT ticker, name FROM stocks").fetchall():
        names[row["ticker"]] = row["name"]

    hits: list[dict] = []
    errors = 0

    for i, ticker in enumerate(tickers, 1):
        if i % 100 == 0:
            logger.info("Progress: %d/%d", i, len(tickers))

        try:
            stock = build_stock_dict(conn, ticker, names.get(ticker, ""))
            if screen_fn(stock):
                hits.append(stock)
        except Exception:
            errors += 1
            logger.debug("Error screening %s", ticker, exc_info=True)

    logger.info(
        "Screening complete: %d hits / %d total (%d errors)",
        len(hits), len(tickers), errors,
    )
    return hits
