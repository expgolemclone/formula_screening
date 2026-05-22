from __future__ import annotations

from datetime import date

from stock_db.api import ensure_prices_fresh as _stock_db_ensure_prices_fresh
from stock_db.sources.price_refresh import PriceRefreshCommandResult


def ensure_prices_fresh(
    *,
    today: date | None = None,
) -> PriceRefreshCommandResult | None:
    del today
    return _stock_db_ensure_prices_fresh()


def ensure_stooq_prices_fresh(
    *,
    today: date | None = None,
) -> PriceRefreshCommandResult | None:
    return ensure_prices_fresh(today=today)
