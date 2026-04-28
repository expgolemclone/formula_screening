from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import requests
from openai import OpenAI
from pypdf import PdfReader

from formula_screening.net_cash import compute_net_cash_metrics

logger = logging.getLogger("formula_screening.validation")

_BALANCE_SHEET_TITLES = (
    "連結貸借対照表",
    "中間連結貸借対照表",
    "四半期連結貸借対照表",
)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_TEXT_CHARS = 40_000
_REQUEST_TIMEOUT = 120


@dataclass(frozen=True)
class ValidationTarget:
    ticker: str
    name: str
    securities_report_url: str
    price: float
    shares_outstanding: int


@dataclass(frozen=True)
class NetCashSnapshot:
    period: str
    market_cap: float | None
    net_cash: float | None
    net_cash_ratio: float | None


@dataclass(frozen=True)
class OCRBalanceSheet:
    status: str
    period: str | None
    period_label: str | None
    reported_unit: str | None
    current_assets: float | None
    current_liabilities: float | None
    non_current_liabilities: float | None
    investment_securities: float | None
    inventories: float | None
    notes: str | None
    source_mode: str


def select_validation_targets(
    conn: sqlite3.Connection,
    limit: int,
) -> list[ValidationTarget]:
    rows = conn.execute(
        """
        WITH latest_price AS (
            SELECT ticker, MAX(date) AS latest_date
            FROM prices
            GROUP BY ticker
        )
        SELECT
            s.ticker,
            s.name,
            s.securities_report_url,
            p.close,
            s.shares_outstanding
        FROM stocks s
        JOIN latest_price lp
          ON lp.ticker = s.ticker
        JOIN prices p
          ON p.ticker = lp.ticker
         AND p.date = lp.latest_date
        WHERE s.securities_report_url IS NOT NULL
          AND s.shares_outstanding IS NOT NULL
          AND p.close IS NOT NULL
        ORDER BY CAST(p.close * s.shares_outstanding AS REAL) DESC, s.ticker
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        ValidationTarget(
            ticker=row["ticker"],
            name=row["name"],
            securities_report_url=row["securities_report_url"],
            price=float(row["close"]),
            shares_outstanding=int(row["shares_outstanding"]),
        )
        for row in rows
    ]


def load_latest_irbank_bs(
    conn: sqlite3.Connection,
    ticker: str,
) -> tuple[str | None, dict[str, float | None], str | None]:
    rows = conn.execute(
        """
        SELECT period, statement, item_name, value
        FROM financial_items
        WHERE ticker = ?
          AND source = 'irbank_bs'
        ORDER BY period DESC, statement, item_name
        """,
        (ticker,),
    ).fetchall()
    if not rows:
        return None, {}, "scrape_missing"

    status_rows = [row for row in rows if row["statement"] == "_status"]
    data_rows = [row for row in rows if row["statement"] == "bs"]
    if data_rows:
        latest_period = max(row["period"] for row in data_rows)
        bs = {
            row["item_name"]: row["value"]
            for row in data_rows
            if row["period"] == latest_period
        }
        return latest_period, bs, None
    if status_rows:
        return None, {}, f"scrape_{status_rows[0]['item_name']}"
    return None, {}, "scrape_missing"


def build_net_cash_snapshot(
    period: str,
    bs: dict[str, float | None],
    price: float | None,
    shares_outstanding: int | None,
) -> NetCashSnapshot:
    metrics = compute_net_cash_metrics(bs, price, shares_outstanding)
    return NetCashSnapshot(
        period=period,
        market_cap=metrics["market_cap"],
        net_cash=metrics["net_cash"],
        net_cash_ratio=metrics["net_cash_ratio"],
    )


def scrape_validation_sample(
    conn: sqlite3.Connection,
    tickers: list[str],
) -> tuple[int, int]:
    from stock_db.browser_client.client import BrowserServiceClient
    from stock_db.paths import cli_defaults, magic_numbers
    from stock_db.sources.irbank.bs_scraper import scrape_and_store

    defaults = cli_defaults("scrape_irbank_bs")
    browser_cfg = magic_numbers()["browser"]
    client_cfg = {
        "pool_size": defaults.get("pool_size", 1),
        "page_timeout": browser_cfg.get("page_timeout", 30000),
        "idle_timeout": browser_cfg.get("idle_timeout", 60000),
        "startup_timeout": browser_cfg.get("startup_timeout", 30),
        "headless": defaults.get("headless", False),
        "disable_xvfb": defaults.get("disable_xvfb", True),
        "challenge_poll_interval_ms": browser_cfg.get("challenge_poll_interval_ms", 500),
        "challenge_clear_stable_ms": browser_cfg.get("challenge_clear_stable_ms", 2000),
    }

    with BrowserServiceClient(config=client_cfg) as client:
        return scrape_and_store(
            client,
            conn,
            tickers,
            proxy=None,
            skip_existing=False,
        )


def _download_pdf_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content


def _extract_pdf_pages(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return pages


def select_balance_sheet_text(pages: list[str]) -> str | None:
    selected: list[str] = []
    seen_indices: set[int] = set()
    for idx, text in enumerate(pages):
        if not any(title in text for title in _BALANCE_SHEET_TITLES):
            continue
        for candidate in (idx, idx + 1):
            if candidate >= len(pages) or candidate in seen_indices:
                continue
            page_text = pages[candidate].strip()
            if not page_text:
                continue
            selected.append(page_text)
            seen_indices.add(candidate)
        break

    if not selected:
        return None

    merged = "\n\n----- PAGE BREAK -----\n\n".join(selected)
    trimmed = merged[:_MAX_TEXT_CHARS].strip()
    return trimmed or None


def _build_extraction_prompt(target_period: str) -> str:
    return (
        "以下の日本企業の有価証券報告書または四半期報告書から、"
        f"連結BSの対象期間 {target_period} の数値だけを抽出してください。"
        "必ず連結貸借対照表を優先し、個別財務諸表は使わないでください。"
        "出力はJSONオブジェクトのみ。period は YYYY-MM 形式。"
        "reported_unit には 元の表示単位（円, 千円, 百万円 など）を入れる。"
        "数値はすべて円換算で返す。"
        "inventories は棚卸資産系の合計で、商品、製品、棚卸資産、販売用不動産、"
        "信託販売用不動産、仕掛販売用不動産、未成工事支出金、開発事業等支出金などを合算する。"
        "investment_securities は投資有価証券。"
        "current_assets/current_liabilities/non_current_liabilities が見つからない場合は status を missing_items にする。"
        "対象期間が見つからない場合は status を period_not_found にする。"
        "棚卸資産系や投資有価証券の該当行が存在しない場合は 0 を返してよい。"
        'JSON schema: {"status":"ok|period_not_found|missing_items","period":"YYYY-MM|null","period_label":"string|null",'
        '"reported_unit":"string|null","current_assets":number|null,"current_liabilities":number|null,'
        '"non_current_liabilities":number|null,"investment_securities":number|null,"inventories":number|null,'
        '"notes":"string|null"}'
    )


def _response_text_to_payload(text: str) -> dict[str, object]:
    match = _JSON_BLOCK_RE.search(text)
    if match is None:
        raise ValueError("OpenAI response did not contain a JSON object")
    return json.loads(match.group(0))


def _extract_via_openai(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    text_context: str | None,
    file_url: str | None,
) -> tuple[dict[str, object], str]:
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    source_mode = "pdf_file"
    if text_context is not None:
        content.append({"type": "input_text", "text": text_context})
        source_mode = "text_excerpt"
    elif file_url is not None:
        content.append({"type": "input_file", "file_url": file_url})
    else:
        raise ValueError("Either text_context or file_url is required")

    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    response_text = getattr(response, "output_text", None)
    if not response_text:
        raise ValueError("OpenAI response had no output_text")
    return _response_text_to_payload(response_text), source_mode


def extract_ocr_balance_sheet(
    client: OpenAI,
    model: str,
    target: ValidationTarget,
    target_period: str,
) -> OCRBalanceSheet:
    prompt = _build_extraction_prompt(target_period)
    try:
        pdf_bytes = _download_pdf_bytes(target.securities_report_url)
        pages = _extract_pdf_pages(pdf_bytes)
        text_context = select_balance_sheet_text(pages)
        payload, source_mode = _extract_via_openai(
            client,
            model,
            prompt,
            text_context=text_context,
            file_url=None if text_context is not None else target.securities_report_url,
        )
    except requests.RequestException as exc:
        logger.warning("%s: failed to download PDF: %s", target.ticker, exc)
        return OCRBalanceSheet("ocr_error", None, None, None, None, None, None, None, None, str(exc), "download")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: OCR extraction failed: %s", target.ticker, exc)
        return OCRBalanceSheet("ocr_error", None, None, None, None, None, None, None, None, str(exc), "openai")

    return OCRBalanceSheet(
        status=str(payload.get("status") or "ocr_error"),
        period=str(payload["period"]) if payload.get("period") is not None else None,
        period_label=str(payload["period_label"]) if payload.get("period_label") is not None else None,
        reported_unit=str(payload["reported_unit"]) if payload.get("reported_unit") is not None else None,
        current_assets=_to_float(payload.get("current_assets")),
        current_liabilities=_to_float(payload.get("current_liabilities")),
        non_current_liabilities=_to_float(payload.get("non_current_liabilities")),
        investment_securities=_to_float(payload.get("investment_securities")),
        inventories=_to_float(payload.get("inventories")),
        notes=str(payload["notes"]) if payload.get("notes") is not None else None,
        source_mode=source_mode,
    )


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def compare_net_cash_snapshots(
    scrape: NetCashSnapshot,
    ocr: NetCashSnapshot,
    *,
    max_delta: float,
) -> tuple[str, float | None]:
    if scrape.period != ocr.period:
        return "period_mismatch", None
    if scrape.net_cash_ratio is None or ocr.net_cash_ratio is None:
        return "missing_metrics", None
    delta = abs(scrape.net_cash_ratio - ocr.net_cash_ratio)
    if delta > max_delta:
        return "delta_exceeded", delta
    return "ok", delta


def write_validation_csv(
    output_path: Path,
    rows: list[dict[str, object]],
) -> None:
    fieldnames = [
        "ticker",
        "name",
        "scrape_period",
        "scrape_market_cap",
        "scrape_net_cash",
        "scrape_nc_ratio",
        "ocr_period",
        "ocr_market_cap",
        "ocr_net_cash",
        "ocr_nc_ratio",
        "delta",
        "status",
        "ocr_status",
        "ocr_source_mode",
        "reported_unit",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_net_cash_with_ocr(
    conn: sqlite3.Connection,
    *,
    sample_size: int,
    model: str,
    output_csv: Path,
    max_delta: float = 0.01,
) -> dict[str, int]:
    targets = select_validation_targets(conn, sample_size)
    if not targets:
        write_validation_csv(output_csv, [])
        return {"sample_size": 0, "scrape_ok": 0, "scrape_errors": 0, "comparable": 0, "matched": 0}

    scrape_ok, scrape_errors = scrape_validation_sample(conn, [target.ticker for target in targets])

    client = OpenAI()
    rows: list[dict[str, object]] = []
    comparable = 0
    matched = 0

    for index, target in enumerate(targets, 1):
        logger.info("[%d/%d] Validating %s", index, len(targets), target.ticker)
        scrape_period, scrape_bs, scrape_status = load_latest_irbank_bs(conn, target.ticker)
        row: dict[str, object] = {
            "ticker": target.ticker,
            "name": target.name,
            "scrape_period": scrape_period,
            "scrape_market_cap": None,
            "scrape_net_cash": None,
            "scrape_nc_ratio": None,
            "ocr_period": None,
            "ocr_market_cap": None,
            "ocr_net_cash": None,
            "ocr_nc_ratio": None,
            "delta": None,
            "status": scrape_status or "pending",
            "ocr_status": None,
            "ocr_source_mode": None,
            "reported_unit": None,
            "notes": None,
        }

        if scrape_status is not None or scrape_period is None:
            rows.append(row)
            continue

        scrape_snapshot = build_net_cash_snapshot(
            scrape_period,
            scrape_bs,
            target.price,
            target.shares_outstanding,
        )
        row["scrape_market_cap"] = scrape_snapshot.market_cap
        row["scrape_net_cash"] = scrape_snapshot.net_cash
        row["scrape_nc_ratio"] = scrape_snapshot.net_cash_ratio

        ocr_bs = extract_ocr_balance_sheet(client, model, target, scrape_period)
        row["ocr_status"] = ocr_bs.status
        row["ocr_source_mode"] = ocr_bs.source_mode
        row["reported_unit"] = ocr_bs.reported_unit
        row["notes"] = ocr_bs.notes
        row["ocr_period"] = ocr_bs.period

        if ocr_bs.status != "ok" or ocr_bs.period is None:
            row["status"] = ocr_bs.status
            rows.append(row)
            continue

        ocr_snapshot = build_net_cash_snapshot(
            ocr_bs.period,
            {
                "current_assets": ocr_bs.current_assets,
                "current_liabilities": ocr_bs.current_liabilities,
                "non_current_liabilities": ocr_bs.non_current_liabilities,
                "investment_securities": ocr_bs.investment_securities,
                "inventories": ocr_bs.inventories,
            },
            target.price,
            target.shares_outstanding,
        )
        row["ocr_market_cap"] = ocr_snapshot.market_cap
        row["ocr_net_cash"] = ocr_snapshot.net_cash
        row["ocr_nc_ratio"] = ocr_snapshot.net_cash_ratio

        status, delta = compare_net_cash_snapshots(
            scrape_snapshot,
            ocr_snapshot,
            max_delta=max_delta,
        )
        row["status"] = status
        row["delta"] = delta

        if status in {"ok", "delta_exceeded"}:
            comparable += 1
        if status == "ok":
            matched += 1

        rows.append(row)

    write_validation_csv(output_csv, rows)
    return {
        "sample_size": len(targets),
        "scrape_ok": scrape_ok,
        "scrape_errors": scrape_errors,
        "comparable": comparable,
        "matched": matched,
    }
