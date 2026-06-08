"""Web UI integration: serves screening results via stock_web_ui."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from stock_web_ui.config import ServerConfig
from stock_web_ui.handler import ApiHandler, json_route
from stock_web_ui.page import IndexPage, render_index_html
from stock_web_ui.serve import serve as _serve
from formula_screening.stock_db_compat import (
    get_balance_sheet_histories,
    get_balance_sheet_history,
    get_stock_price_metadata,
)

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
                     "total_payout_ratio", "retained_earnings_ratio",
                     "provision_for_directors_retirement_benefits",
                     "has_preferred_shares", "has_potential_equity",
                     "potential_common_shares", "has_unquantified_potential_equity",
                     "diluted_eps_common_share_increase", "croic",
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


def _balance_sheet_history_route(query_params: dict[str, list[str]]) -> dict[str, object]:
    code_values = query_params.get("code", [])
    code = code_values[0].strip() if code_values else ""
    if not code:
        return {"error": "missing_code"}
    return get_balance_sheet_history(code)


def create_screening_payload_api(payload: list[dict]) -> dict[str, ApiHandler]:
    """Create API routes from an already serialized Rust payload."""

    metadata: StockPriceMetadata = build_stock_price_metadata()
    return {
        "/api/screening": json_route(lambda _params: payload),
        "/api/stock-price-meta": json_route(lambda _params: metadata),
        "/api/balance-sheet": json_route(_balance_sheet_history_route),
    }


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


def save_index_html(
    path: Path,
    *,
    asset_version: str,
    shared_asset_base_url: str,
) -> None:
    """Save the GitHub Pages index.html rendered from stock_web_ui's template."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        render_index_html(
            IndexPage(
                title="Formula Screening",
                loading_message="スクリーニング結果を読み込み中です。",
                tab_aria_label="タブ切替",
                asset_version=asset_version,
                shared_asset_base_url=shared_asset_base_url,
            )
        )
    )


def save_screening_payload_json(payload: list[dict], path: Path) -> None:
    """Save an already serialized Rust payload as static JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_stock_price_metadata_json(path: Path) -> None:
    """Save the latest stock price date metadata as static JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata: StockPriceMetadata = build_stock_price_metadata()
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def save_balance_sheet_history_json(payload: list[dict], directory: Path) -> list[Path]:
    """Save one balance-sheet history JSON per stock code for GitHub Pages."""

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    seen: set[str] = set()
    codes: list[str] = []
    for row in payload:
        code = str(row.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    histories = get_balance_sheet_histories(codes)
    for code in codes:
        path = directory / f"{code}.json"
        history = histories.get(code)
        if not isinstance(history, dict):
            raise ValueError(f"missing balance-sheet history for {code}")
        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written
