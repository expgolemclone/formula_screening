"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import yfinance as yf

from formula_screening.config import MAGIC
from formula_screening.db.repository import (
    get_latest_price_with_shares,
    upsert_price,
)
from formula_screening.stealth import (
    ProxyPool,
    create_session,
    random_delay,
)

logger = logging.getLogger("formula_screening.yfinance_price")

_MAX_RETRIES = MAGIC["price"]["max_retries"]


def is_price_stale(updated_at: str | None) -> bool:
    """Return True if the cached price is older than 1 day or missing."""
    if updated_at is None:
        return True
    try:
        ts = datetime.fromisoformat(updated_at)
        return datetime.now(timezone.utc) - ts > timedelta(days=MAGIC["price"]["stale_days"])
    except ValueError:
        return True


def _fetch_one(
    ticker: str,
    pool: ProxyPool,
) -> dict[str, float | int | None]:
    """Fetch price and shares for a single ticker, with retry on rate-limit."""
    symbol = f"{ticker}.T"

    for attempt in range(_MAX_RETRIES):
        try:
            session = create_session(pool)
            t = yf.Ticker(symbol, session=session)
            hist = t.history(period="1d", raise_errors=True)
            price: float | None = float(hist["Close"].iloc[-1]) if not hist.empty else None
            fi = t.fast_info
            shares_raw = fi.get("shares")
            shares: int | None = int(shares_raw) if shares_raw is not None else None
            return {"price": price, "shares_outstanding": shares}
        except yf.exceptions.YFRateLimitError:
            logger.info("Rate-limited for %s (attempt %d), rotating...", symbol, attempt + 1)
            pool.report_failure()
            random_delay(
                MAGIC["price"]["rate_limit_delay_min"],
                MAGIC["price"]["rate_limit_delay_max"],
            )
            continue
        except yf.exceptions.YFPricesMissingError:
            return {"price": None, "shares_outstanding": None}
        except Exception:
            logger.debug("Failed to fetch %s (attempt %d)", symbol, attempt + 1, exc_info=True)
            pool.report_failure()
            continue

    return {"price": None, "shares_outstanding": None}


def fetch_prices_worker(
    tickers: list[str],
    pool: ProxyPool,
    *,
    interval: float,
    force: bool,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Worker function for dispatch_scrape_workers.

    Fetches price + shares for each ticker in the chunk via its own
    proxy sub-pool, with delays between requests.
    """
    from formula_screening.db.schema import get_connection

    conn = get_connection()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        for ticker in tickers:
            with stats_lock:
                counter[0] += 1
                seq = counter[0]

            if not force:
                cached = get_latest_price_with_shares(conn, ticker)
                if not is_price_stale(cached["updated_at"]):
                    with stats_lock:
                        stats["skip"] += 1
                    continue

            result = _fetch_one(ticker, pool)
            price = result["price"]
            shares = result["shares_outstanding"]

            if price is None and shares is None:
                with stats_lock:
                    stats["fail"] += 1
                    print(f"[{seq}/{total}] {ticker} FAILED", flush=True)
                random_delay(interval, interval + MAGIC["price"]["interval_jitter"])
                continue

            upsert_price(
                conn, ticker, today,
                close=price,
                volume=None,
                shares_outstanding=shares,
            )
            conn.commit()

            with stats_lock:
                stats["ok"] += 1
                print(f"[{seq}/{total}] {ticker} OK", flush=True)

            random_delay(interval, interval + MAGIC["price"]["interval_jitter"])
    finally:
        conn.close()
