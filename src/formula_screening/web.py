"""Web UI integration: serves screening results via stock_web_ui."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from stock_web_ui.config import ServerConfig
from stock_web_ui.handler import ApiHandler
from stock_web_ui.serve import serve as _serve

from formula_screening.indicators import croic, fcf_yield_avg

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR: Path = _PROJECT_ROOT / "docs"
_STATIC_ROOT: Path = _DOCS_DIR / "assets"
_INDEX_PATH: Path = _DOCS_DIR / "index.html"


def create_screening_api(stocks: list[dict]) -> dict[str, ApiHandler]:
    """Create API routes that expose screening results as JSON.

    Args:
        stocks: List of stock dicts from run_screening().

    Returns:
        Dict mapping route paths to handler callables.
    """
    payload: bytes = json.dumps(
        [_serialize_stock(s) for s in stocks],
        ensure_ascii=False,
    ).encode("utf-8")

    def handle_screening(handler: BaseHTTPRequestHandler, _params: dict) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    return {"/api/screening": handle_screening}


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
        index_path=_INDEX_PATH,
        server_config=server_config,
        api_routes=api_routes,
    )


def _serialize_stock(stock: dict) -> dict:
    """Convert a screener stock dict to the JSON shape expected by app.js."""
    metrics = stock.get("metrics", {})

    fcf_value = fcf_yield_avg(stock)
    croic_value = croic(stock)

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
    }
