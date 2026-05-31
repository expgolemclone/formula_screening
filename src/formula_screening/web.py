"""Web UI integration: serves screening results via stock_web_ui."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from stock_web_ui.config import ServerConfig
from stock_web_ui.handler import ApiHandler, json_route
from stock_web_ui.page import IndexPage
from stock_web_ui.serve import serve as _serve
from formula_screening.stock_db_compat import get_stock_price_metadata
from formula_screening.indicators import (
    croic,
    fcf_yield_avg,
    peg_blended_2f_with_status,
    peg_trailing_with_status,
)
from formula_screening.preferred_shares import preferred_share_flag

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR: Path = _PROJECT_ROOT / "docs"
_STATIC_ROOT: Path = _DOCS_DIR / "assets"
_HANDBOOK_DATA_DIR: Path = _PROJECT_ROOT.parent / "japan_company_handbook" / "data"

StockMetricValue = float | bool | str | None
StockPriceMetadata = dict[str, str | None]


def compute_all_stock_metrics(
    conn: object | None = None,
) -> dict[str, dict[str, StockMetricValue]]:
    """Compute enriched metrics for all tickers via the Rust-backed public API.

    This is the single entry-point for external projects that need per-ticker
    screening metrics. Callers should use this instead of importing internal
    modules directly. ``conn`` is retained only to fail loudly for old callers;
    connection injection is no longer part of the public API.

    Returns:
        ``{ticker: {"price", "price_date", "net_cash_ratio", "per_actual", "per", "per_next",
                     "fcf_yield_avg", "equity_ratio", "peg_trailing_5",
                     "peg_trailing_5_status", "peg_blended_5y_actual_2f",
                     "peg_blended_5y_actual_2f_status", "dividend_yield",
                     "total_payout_ratio", "has_preferred_shares", "croic",
                     "pbr", "market_cap"}}``
    """
    if conn is not None:
        msg = "compute_all_stock_metrics no longer accepts sqlite connections"
        raise TypeError(msg)

    from formula_screening._core import compute_all_stock_metrics as _compute_all_stock_metrics

    return _compute_all_stock_metrics()


def run_screening_strategy_payload(
    strategy_path: Path | str,
    *,
    tickers: Sequence[str] | None = None,
    return_all: bool = False,
) -> list[dict]:
    """Run a TOML strategy and return the Rust-backed public screening payload.

    External projects should use this API instead of importing ``_core``
    directly. ``formula_screening`` owns strategy execution; downstream
    projects can pass candidate tickers and merge the returned payload with
    their own domain data.
    """
    from formula_screening._core import run_screening_payload_py

    ticker_list = None if tickers is None else [str(ticker) for ticker in tickers]
    return run_screening_payload_py(
        str(Path(strategy_path)),
        ticker_list,
        return_all,
    )


def build_stock_price_metadata() -> StockPriceMetadata:
    """Return stock price dates for UI status and stale-row display."""

    return get_stock_price_metadata()


def create_screening_api(stocks: list[dict]) -> dict[str, ApiHandler]:
    """Create API routes that expose screening results as JSON.

    Args:
        stocks: List of stock dicts from run_screening().

    Returns:
        Dict mapping route paths to handler callables.
    """
    payload: list[dict] = [_serialize_stock(s) for s in stocks]
    metadata: StockPriceMetadata = build_stock_price_metadata()

    return {
        "/api/screening": json_route(lambda _params: payload),
        "/api/stock-price-meta": json_route(lambda _params: metadata),
    }


def create_screening_payload_api(payload: list[dict]) -> dict[str, ApiHandler]:
    """Create API routes from an already serialized Rust payload."""

    metadata: StockPriceMetadata = build_stock_price_metadata()
    return {
        "/api/screening": json_route(lambda _params: payload),
        "/api/stock-price-meta": json_route(lambda _params: metadata),
    }


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


def serve_screening_payload(
    payload: list[dict],
    *,
    server_config: ServerConfig | None = None,
) -> None:
    """Start the web UI server from an already serialized Rust payload."""

    _serve(
        static_root=_STATIC_ROOT,
        index_page=IndexPage(
            title="Formula Screening",
            loading_message="スクリーニング結果を読み込み中です。",
            tab_aria_label="タブ切替",
        ),
        server_config=server_config,
        api_routes=create_screening_payload_api(payload),
        yazi_base_dir=_HANDBOOK_DATA_DIR,
    )


def save_screening_json(stocks: list[dict], path: Path) -> None:
    """Save screening results as a static JSON file for GitHub Pages."""

    payload = [_serialize_stock(s) for s in stocks]
    save_screening_payload_json(payload, path)


def save_screening_payload_json(payload: list[dict], path: Path) -> None:
    """Save an already serialized Rust payload as static JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_stock_price_metadata_json(path: Path) -> None:
    """Save the latest stock price date metadata as static JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata: StockPriceMetadata = build_stock_price_metadata()
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_stock(stock: dict) -> dict:
    """Convert a screener stock dict to the JSON shape expected by app.js."""
    metrics = stock.get("metrics", {})

    fcf_value = fcf_yield_avg(stock)
    croic_value = croic(stock)
    peg_trailing_5_result = peg_trailing_with_status(stock, 5)
    peg_blended_result = peg_blended_2f_with_status(stock, 5)

    return {
        "code": stock.get("ticker", ""),
        "name": stock.get("name", ""),
        "price": stock.get("price"),
        "price_date": stock.get("price_date"),
        "metrics": {
            "net_cash_ratio": metrics.get("net_cash_ratio"),
            "per_actual": metrics.get("per_actual"),
            "per": metrics.get("per"),
            "per_next": metrics.get("per_next"),
            "equity_ratio": metrics.get("equity_ratio"),
            "dividend_yield": metrics.get("dividend_yield"),
            "total_payout_ratio": metrics.get("total_payout_ratio"),
            "pbr": metrics.get("pbr"),
            "market_cap": metrics.get("market_cap"),
        },
        "fcf_yield_avg": fcf_value,
        "peg_trailing_5": peg_trailing_5_result.value,
        "peg_trailing_5_status": peg_trailing_5_result.status,
        "peg_blended_5y_actual_2f": peg_blended_result.value,
        "peg_blended_5y_actual_2f_status": peg_blended_result.status,
        "has_preferred_shares": preferred_share_flag(stock),
        "croic": croic_value,
        "cf_history": stock.get("cf_history"),
    }
