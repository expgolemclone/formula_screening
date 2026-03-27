"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import logging

import yfinance as yf

logger = logging.getLogger("formula_screening.yfinance_price")


def fetch_current(ticker: str) -> dict[str, float | None]:
    """Fetch the latest price and shares outstanding for a Japanese stock.

    Args:
        ticker: Bare ticker code (e.g. "7203"). The ".T" suffix is appended
                automatically for Tokyo Stock Exchange.

    Returns:
        {"price": float | None, "shares_outstanding": int | None}
    """
    symbol = f"{ticker}.T"
    logger.debug("Fetching price for %s", symbol)

    try:
        t = yf.Ticker(symbol)
        info = t.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = info.get("sharesOutstanding")

        if price is not None:
            price = float(price)
        if shares is not None:
            shares = int(shares)

        return {"price": price, "shares_outstanding": shares}
    except Exception:
        logger.warning("Failed to fetch price for %s", symbol, exc_info=True)
        return {"price": None, "shares_outstanding": None}


def fetch_current_batch(tickers: list[str]) -> dict[str, dict[str, float | None]]:
    """Fetch prices for multiple tickers.

    Returns:
        {ticker: {"price": ..., "shares_outstanding": ...}}
    """
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_current(ticker)
    return results
