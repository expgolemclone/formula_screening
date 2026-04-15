"""Data access layer — delegates to stock_db.storage modules."""

from stock_db.storage.financials import (  # noqa: F401
    get_cached_periods,
    get_financial_dict,
    get_historical_items,
    upsert_financial_item,
    upsert_financial_items_bulk,
)
from stock_db.storage.market_caps import upsert_market_cap  # noqa: F401
from stock_db.storage.prices import (  # noqa: F401
    PriceWithShares,
    get_fresh_price_tickers,
    get_latest_price,
    get_latest_price_with_shares,
    get_tickers_with_shares,
    is_price_stale,
    upsert_price,
    upsert_shares_outstanding,
)
from stock_db.storage.stocks import (  # noqa: F401
    get_all_tickers,
    get_edinet_code,
    get_existing_tickers,
    get_stock_names,
    get_ticker_edinet_map,
    upsert_company_metadata,
    upsert_stock,
)
