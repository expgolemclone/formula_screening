"""Web UI integration: serves screening results via stock_web_ui."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stock_db.paths import STOCKS_DB_PATH
from stock_db.storage.connection import get_connection
from stock_db.storage.financials import get_financial_dict, get_historical_items
from stock_db.storage.prices import get_latest_price_with_shares
from stock_db.storage.stocks import get_stock_names
from stock_web_ui.config import ServerConfig
from stock_web_ui.handler import ApiHandler, json_route
from stock_web_ui.page import IndexPage
from stock_web_ui.serve import serve as _serve
from formula_screening.indicators import croic, fcf_yield_avg, peg_5
from formula_screening.indicators.peg import PEG_YEARS
from formula_screening.metrics import compute_metrics

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR: Path = _PROJECT_ROOT / "docs"
_STATIC_ROOT: Path = _DOCS_DIR / "assets"
_HANDBOOK_DATA_DIR: Path = _PROJECT_ROOT.parent / "japan_company_handbook" / "data"


def compute_all_stock_metrics(
    conn: sqlite3.Connection | None = None,
) -> dict[str, dict[str, float | None]]:
    """Compute enriched metrics for all tickers via the public API.

    This is the single entry-point for external projects that need per-ticker
    screening metrics.  Callers should use this instead of importing internal
    modules (db.repository, indicators, metrics) directly.

    Args:
        conn: Optional existing DB connection.  If *None* a fresh one is
              created and closed automatically.

    Returns:
        ``{ticker: {"price", "net_cash_ratio", "per", "equity_ratio",
                     "fcf_yield_avg", "croic", "peg_5", "market_cap"}}``
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection(STOCKS_DB_PATH)
    try:
        names = get_stock_names(conn)
        result: dict[str, dict[str, float | None]] = {}

        for code in names:
            try:
                financials = get_financial_dict(conn, code)
                if not financials:
                    continue
                price_data = get_latest_price_with_shares(conn, code)
                price = price_data["price"]
                shares = price_data["shares_outstanding"]
                metrics = compute_metrics(financials, price, shares)

                stock_dict = {
                    "ticker": code,
                    "price": price,
                    "shares_outstanding": shares,
                    "pl": financials.get("pl", {}),
                    "bs": financials.get("bs", {}),
                    "cf": financials.get("cf", {}),
                    "dividend": financials.get("dividend", {}),
                    "forecast": financials.get("forecast", {}),
                    "metrics": metrics,
                }
                stock_dict["cf_history"] = []
                stock_dict["pl_history"] = get_historical_items(conn, code, "pl", n_periods=PEG_YEARS)

                result[code] = {
                    "price": price,
                    "net_cash_ratio": metrics.get("net_cash_ratio"),
                    "per": metrics.get("per"),
                    "equity_ratio": metrics.get("equity_ratio"),
                    "fcf_yield_avg": fcf_yield_avg(stock_dict),
                    "croic": croic(stock_dict),
                    "peg_5": peg_5(stock_dict),
                    "market_cap": metrics.get("market_cap"),
                }
            except (KeyError, ValueError, ZeroDivisionError, TypeError):
                continue

        return result
    finally:
        if own_conn:
            conn.close()


def create_screening_api(stocks: list[dict]) -> dict[str, ApiHandler]:
    """Create API routes that expose screening results as JSON.

    Args:
        stocks: List of stock dicts from run_screening().

    Returns:
        Dict mapping route paths to handler callables.
    """
    payload: list[dict] = [_serialize_stock(s) for s in stocks]

    return {"/api/screening": json_route(lambda _params: payload)}


def serve_screening(
    stocks: list[dict],
    *,
    server_config: ServerConfig | None = None,
) -> None:
    """Start the web UI server with screening results.

    Args:
        stocks: Screening results to display.
        server_config: Server host/port (loads default if omitted).
    """
    api_routes = create_screening_api(stocks)

    _serve(
        static_root=_STATIC_ROOT,
        index_page=IndexPage(
            title="Formula Screening",
            loading_message="スクリーニング結果を読み込み中です。",
            tab_aria_label="タブ切替",
        ),
        server_config=server_config,
        api_routes=api_routes,
        yazi_base_dir=_HANDBOOK_DATA_DIR,
    )


def _serialize_stock(stock: dict) -> dict:
    """Convert a screener stock dict to the JSON shape expected by app.js."""
    metrics = stock.get("metrics", {})

    fcf_value = fcf_yield_avg(stock)
    croic_value = croic(stock)
    peg_value = peg_5(stock)

    return {
        "code": stock.get("ticker", ""),
        "name": stock.get("name", ""),
        "price": stock.get("price"),
        "metrics": {
            "net_cash_ratio": metrics.get("net_cash_ratio"),
            "per": metrics.get("per"),
            "pbr": metrics.get("pbr"),
            "dividend_yield": metrics.get("dividend_yield"),
            "equity_ratio": metrics.get("equity_ratio"),
            "market_cap": metrics.get("market_cap"),
        },
        "fcf_yield_avg": fcf_value,
        "croic": croic_value,
        "peg_5": peg_value,
    }
