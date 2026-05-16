from __future__ import annotations

from datetime import date
from pathlib import Path

from stock_db.paths import STOCKS_DB_PATH
from stock_db.sources.stooq import StooqPriceUpdateCommandResult, run_stooq_price_update_command
from stock_db.storage.connection import get_connection
from stock_db.storage.prices import is_stooq_price_update_required


def ensure_stooq_prices_fresh(
    *,
    db_path: Path = STOCKS_DB_PATH,
    today: date | None = None,
) -> StooqPriceUpdateCommandResult | None:
    conn = get_connection(db_path)
    try:
        update_required = is_stooq_price_update_required(conn, today=today)
    finally:
        conn.close()

    if not update_required:
        return None

    return run_stooq_price_update_command()
