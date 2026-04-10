"""Fetch current stock price and shares outstanding from yfinance."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests as _requests
import yfinance as yf

from formula_screening.config import MAGIC
from formula_screening.stealth import (
    ProxyPool,
    ProxyUnavailableError,
    random_delay,
    random_ua,
)

logger: logging.Logger = logging.getLogger("formula_screening.yfinance_price")

_MAX_RETRIES: int = MAGIC["price"]["max_retries"]


def is_price_stale(updated_at: str | None) -> bool:
    """Return True if the cached price is older than 1 day or missing."""
    if updated_at is None:
        return True
    try:
        ts = datetime.fromisoformat(updated_at)
        return datetime.now(timezone.utc) - ts > timedelta(days=MAGIC["price"]["stale_days"])
    except ValueError:
        return True


def _create_yf_session(pool: ProxyPool) -> _requests.Session:
    """Create a ``requests.Session`` with proxy from the pool for yfinance."""
    session: _requests.Session = _requests.Session()
    proxy_url: str | None = pool.get()
    if proxy_url is None:
        raise ProxyUnavailableError("Proxy pool exhausted during request execution")
    session.proxies = {"http": proxy_url, "https": proxy_url}
    session.headers["User-Agent"] = random_ua()
    return session


def _fetch_one(
    ticker: str,
    pool: ProxyPool,
) -> dict[str, float | int | None]:
    """Fetch price and shares for a single ticker, with retry on rate-limit."""
    symbol: str = f"{ticker}.T"

    for attempt in range(_MAX_RETRIES):
        try:
            session: _requests.Session = _create_yf_session(pool)
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
        except ProxyUnavailableError:
            raise
        except Exception:
            logger.debug("Failed to fetch %s (attempt %d)", symbol, attempt + 1, exc_info=True)
            pool.report_failure()
            continue

    return {"price": None, "shares_outstanding": None}
