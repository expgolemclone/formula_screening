from __future__ import annotations

from datetime import date
from pathlib import Path

from stock_db.paths import STOCKS_DB_PATH
from stock_db.sources.price_refresh import (
    PriceRefreshCommandResult,
    run_price_refresh_command,
)


def ensure_prices_fresh(
    *,
    db_path: Path = STOCKS_DB_PATH,
    today: date | None = None,
) -> PriceRefreshCommandResult | None:
    del today
    return run_price_refresh_command(db_path=db_path, if_needed=True)


def ensure_stooq_prices_fresh(
    *,
    db_path: Path = STOCKS_DB_PATH,
    today: date | None = None,
) -> PriceRefreshCommandResult | None:
    return ensure_prices_fresh(db_path=db_path, today=today)
