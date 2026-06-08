"""stock_db Rust CLI boundary used by formula_screening."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
ScreeningStock: TypeAlias = dict[str, JsonValue]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STOCK_DB_ROOT = _PROJECT_ROOT.parent / "stock_db"


@dataclass(frozen=True, slots=True)
class PriceRefreshCommandResult:
    stdout: str
    stderr: str


class PriceRefreshError(RuntimeError):
    """Raised when stock_db cannot refresh stale prices."""


def ensure_prices_fresh() -> PriceRefreshCommandResult | None:
    result = subprocess.run(
        ["uv", "run", "refresh-prices", "--if-needed", "--headless"],
        cwd=_stock_db_root(),
        env=_stock_db_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise PriceRefreshError(message or f"refresh-prices exited {result.returncode}")
    if not result.stdout and not result.stderr:
        return None
    return PriceRefreshCommandResult(stdout=result.stdout, stderr=result.stderr)


def get_all_tickers() -> list[str]:
    return _expect_list(_run_stock_db_json(["downstream-all-tickers"]))


def get_stock_names() -> dict[str, str]:
    raw = _expect_dict(_run_stock_db_json(["downstream-stock-names"]))
    return {str(code): str(name) for code, name in raw.items()}


def get_stock_price_metadata() -> dict[str, str | None]:
    ensure_prices_fresh()
    raw = _expect_dict(_run_stock_db_json(["downstream-stock-price-metadata"]))
    return {
        "price_date": _optional_str(raw.get("price_date")),
        "target_price_date": str(raw["target_price_date"]),
    }


def get_screening_tickers(*, limit: int | None = None) -> list[str]:
    args = ["downstream-screening-tickers"]
    if limit is not None:
        args.extend(["--limit", str(limit)])
    return _expect_list(_run_stock_db_json(args))


def load_screening_stocks(
    tickers: Sequence[str] | None = None,
    *,
    fcf_periods: int = 10,
    pl_periods: int = 6,
    payout_periods: int = 10,
) -> list[ScreeningStock]:
    args = [
        "downstream-screening-stocks",
        "--fcf-periods",
        str(fcf_periods),
        "--pl-periods",
        str(pl_periods),
        "--payout-periods",
        str(payout_periods),
    ]
    raw = _expect_list(_run_stock_db_json(args, tickers=tickers or []))
    return [_normalize_screening_stock(row) for row in raw]


def get_balance_sheet_histories(
    tickers: Sequence[str],
    *,
    n_periods: int = 10,
    source: str = "edinet_xbrl",
) -> dict[str, JsonValue]:
    args = [
        "downstream-balance-sheet-histories",
        "--n-periods",
        str(n_periods),
        "--source",
        source,
    ]
    raw = _expect_dict(_run_stock_db_json(args, tickers=tickers))
    return {str(code): value for code, value in raw.items()}


def get_balance_sheet_history(
    ticker: str,
    *,
    n_periods: int = 10,
    source: str = "edinet_xbrl",
) -> dict[str, JsonValue]:
    code = str(ticker).strip()
    if not code:
        raise ValueError("ticker must not be empty")
    histories = get_balance_sheet_histories([code], n_periods=n_periods, source=source)
    history = histories.get(code)
    if isinstance(history, dict):
        return history
    raise ValueError(f"missing balance-sheet history for {code}")


def _stock_db_root() -> Path:
    configured = os.environ.get("STOCK_DB_ROOT")
    root = Path(configured).expanduser() if configured else _DEFAULT_STOCK_DB_ROOT
    if not root.is_dir():
        raise ValueError(f"stock_db root does not exist: {root}")
    return root


def _stock_db_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("STOCK_DB_ROOT", str(_stock_db_root()))
    return env


def _run_stock_db_json(
    args: Sequence[str],
    *,
    tickers: Sequence[str] | None = None,
) -> JsonValue:
    input_text = None if tickers is None else json.dumps(list(tickers))
    result = subprocess.run(
        ["cargo", "run", "-q", "-p", "edinet-xbrl", "--", *args],
        cwd=_stock_db_root(),
        env=_stock_db_env(),
        input=input_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise ValueError(message or f"edinet-xbrl {' '.join(args)} exited {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"edinet-xbrl {' '.join(args)} emitted invalid JSON") from exc


def _expect_list(value: JsonValue) -> list:
    if not isinstance(value, list):
        raise ValueError(f"expected JSON list, got {type(value).__name__}")
    return value


def _expect_dict(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object, got {type(value).__name__}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_screening_stock(row: object) -> ScreeningStock:
    if not isinstance(row, dict):
        raise ValueError(f"screening stock row must be a JSON object: {row!r}")
    result: ScreeningStock = dict(row)
    for key in ("cf_history", "pl_history", "dividend_history"):
        result[key] = _normalize_history_items(result.get(key))
    return result


def _normalize_history_items(value: object) -> list[tuple[str, dict[str, JsonValue]]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, dict[str, JsonValue]]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period", ""))
        items = item.get("items")
        result.append((period, dict(items) if isinstance(items, dict) else {}))
    return result


__all__ = [
    "PriceRefreshCommandResult",
    "PriceRefreshError",
    "ScreeningStock",
    "ensure_prices_fresh",
    "get_all_tickers",
    "get_balance_sheet_histories",
    "get_balance_sheet_history",
    "get_screening_tickers",
    "get_stock_names",
    "get_stock_price_metadata",
    "load_screening_stocks",
]
