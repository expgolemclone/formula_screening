"""Scrape detailed BS items from IRBank individual stock pages.

IRBank's /bs page embeds Google Charts data in JavaScript with the format:
    gGm([["year", "col1", ...], ["2025年3月", {v:123, f:"..."}, ...]], "debit", ...)

This module extracts the numeric values (v) and normalises Japanese column
names to a canonical English item_name for storage in the DB.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("formula_screening.irbank_bs")

_TITLE_RE = re.compile(r"^(.+?)（\d+[A-Z]?）")


def parse_company_name(html: str) -> str | None:
    """Extract company name from IRBank BS page ``<title>``."""
    m = re.search(r"<title>(.*?)</title>", html)
    if m is None:
        return None
    tm = _TITLE_RE.match(m.group(1))
    return tm.group(1) if tm else None

# --- Column-name normalisation ------------------------------------------------

# Maps Japanese column names (as they appear in gGm chart data) to canonical
# English item names.  Multiple Japanese variants map to the same key so that
# JP-GAAP, IFRS, and US-GAAP companies all land in the same DB column.
_DEBIT_COLUMN_MAP: dict[str, str] = {
    "投資等": "investment_securities",
    "投資及びその他の資産": "investment_securities",
    "有形固定資産": "tangible_fixed_assets",
    "無形固定資産": "intangible_fixed_assets",
    "その他流動資産": "other_current_assets",
    "たな卸資産": "inventories",
    "現金等": "cash_and_deposits",
    "現金及び預金": "cash_and_deposits",
    "現金及び現金同等物": "cash_and_deposits",
    "売上債権": "trade_receivables",
    "その他資産": "other_assets",
}

_CREDIT_COLUMN_MAP: dict[str, str] = {
    "株主資本": "stockholders_equity",
    "親会社所有者帰属持分": "stockholders_equity",
    "親会社の所有者に帰属する持分": "stockholders_equity",
    "その他純資産": "other_equity",
    "固定負債": "non_current_liabilities",
    "その他流動負債": "other_current_liabilities",
    "仕入債務": "trade_payables",
    "その他負債": "other_liabilities",
}

_PERCENTAGE_COLUMN_MAP: dict[str, str] = {
    "固定資産": "fixed_assets",
    "流動資産": "current_assets",
    "純資産": "net_assets",
    "固定負債": "non_current_liabilities_total",
    "流動負債": "current_liabilities",
}

# --- JavaScript parser --------------------------------------------------------

# Matches:  gGm( <array> , "debit"|"credit"|"percentage" , ... )
_GGM_PATTERN = re.compile(
    r'gGm\(\s*(\[[\s\S]*?\])\s*,\s*"(debit|credit|percentage)"',
)

# Matches:  {v:12345, f:"..."}  or  {v: 12345, f: "..."}
_VF_PATTERN = re.compile(r'\{v:\s*(-?\d+(?:\.\d+)?),\s*f:"[^"]*"\}')

# Matches a year label like "2025年3月" or "2025年12月 借方"
_YEAR_PATTERN = re.compile(r'"(\d{4})年(\d{1,2})月')


def _parse_header_row(row_str: str) -> list[str]:
    """Extract column names from the header row string like '["year","col1",...]'."""
    return re.findall(r'"([^"]+)"', row_str)


def _parse_data_rows(array_str: str) -> list[tuple[str, list[float | None]]]:
    """Parse gGm data array into (period, [values...]) tuples.

    Each row looks like:
        ["2025年3月", {v:123, f:"..."}, {v:456, f:"..."}, ...]
    """
    rows: list[tuple[str, list[float | None]]] = []

    # Split by top-level row boundaries: each row starts with ["
    row_strings = re.split(r'(?=\[")', array_str)

    for row_str in row_strings:
        year_match = _YEAR_PATTERN.search(row_str)
        if not year_match:
            continue

        year = year_match.group(1)
        month = year_match.group(2).zfill(2)
        period = f"{year}-{month}"

        values: list[float | None] = []
        for vf_match in _VF_PATTERN.finditer(row_str):
            values.append(float(vf_match.group(1)))

        # Also handle null values in the row
        # Replace {v:..., f:...} with placeholder, then check remaining cells
        cleaned = _VF_PATTERN.sub("__VF__", row_str)
        # Count all cells after the year label
        cells = re.findall(r'(?:__VF__|null)', cleaned)
        if len(cells) > len(values):
            # Re-parse preserving order of values and nulls
            values = []
            for cell in cells:
                if cell == "null":
                    values.append(None)
                else:
                    values.append(None)  # placeholder
            # Fill in actual values
            vf_iter = _VF_PATTERN.finditer(row_str)
            for i, cell in enumerate(cells):
                if cell == "__VF__":
                    m = next(vf_iter, None)
                    if m:
                        values[i] = float(m.group(1))

        if values:
            rows.append((period, values))

    return rows


def parse_bs_charts(html: str) -> dict[str, list[dict]]:
    """Parse all gGm chart data from a BS page HTML.

    Returns:
        dict mapping chart_type ("debit"/"credit"/"percentage") to a list of
        dicts with keys: period, item_name, value.
    """
    result: dict[str, list[dict]] = {}

    for match in _GGM_PATTERN.finditer(html):
        array_str = match.group(1)
        chart_type = match.group(2)

        column_map = {
            "debit": _DEBIT_COLUMN_MAP,
            "credit": _CREDIT_COLUMN_MAP,
            "percentage": _PERCENTAGE_COLUMN_MAP,
        }[chart_type]

        # Extract header row (first [...] in the array)
        header_match = re.search(r'\[([^\]]*"[^\]]*)\]', array_str)
        if not header_match:
            continue

        columns = _parse_header_row(header_match.group(0))
        # First column is always "year" / "年" — skip it
        item_columns = columns[1:]

        data_rows = _parse_data_rows(array_str)
        items: list[dict] = []

        for period, values in data_rows:
            for i, val in enumerate(values):
                if i >= len(item_columns):
                    break
                if val is None:
                    continue
                jp_name = item_columns[i]
                en_name = column_map.get(jp_name)
                if en_name is None:
                    logger.debug("Unmapped BS column: %s (chart=%s)", jp_name, chart_type)
                    continue
                items.append({
                    "period": period,
                    "item_name": en_name,
                    "value": val,
                })

        result[chart_type] = items

    return result


# --- HTTP fetch ---------------------------------------------------------------


def _validate_bs_html(html: str) -> bool:
    """Return True if the HTML contains gGm chart data."""
    return "gGm(" in html


def fetch_bs_html(
    ticker: str,
    pool: object,
    *,
    timeout: int = 15,
) -> str | None:
    """Fetch /bs page HTML using requests + ProxyPool.

    Args:
        ticker: Stock ticker code.
        pool: A ``ProxyPool`` instance (uses ``.get()`` and ``.report_failure()``).
        timeout: HTTP request timeout in seconds.

    Returns:
        HTML string if successful, None on failure.
    """
    from formula_screening.datasources.irbank_common import fetch_irbank_html

    return fetch_irbank_html(
        ticker, "bs", pool, validate_fn=_validate_bs_html, timeout=timeout,
    )


# --- Row building (shared between script and CLI) ----------------------------


def build_bs_rows(
    ticker: str,
    html: str,
    *,
    years: int | None = None,
) -> list[dict]:
    """Parse BS page HTML and return rows ready for DB upsert.

    Merges percentage/debit/credit charts, deduplicates by (period, item_name),
    and optionally limits to the most recent *years* periods.

    Returns a list of dicts with keys:
        ticker, period, statement, item_name, value, source
    """
    charts = parse_bs_charts(html)

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # Prefer percentage chart for aggregate items (current_assets, etc.)
    # then debit/credit for detailed items
    for chart_type in ("percentage", "debit", "credit"):
        for item in charts.get(chart_type, []):
            key = (item["period"], item["item_name"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "ticker": ticker,
                "period": item["period"],
                "statement": "bs",
                "item_name": item["item_name"],
                "value": item["value"],
                "source": "irbank_bs",
            })

    if years is not None and rows:
        periods = sorted({r["period"] for r in rows}, reverse=True)
        keep = set(periods[:years])
        rows = [r for r in rows if r["period"] in keep]

    return rows


# --- Parallel worker (shared between script and CLI) -------------------------


def _on_bs_html(ticker: str, html: str, conn: object) -> None:
    """Extract company name from BS page and upsert into stocks table."""
    from formula_screening.db.repository import upsert_stock

    name = parse_company_name(html)
    if name:
        upsert_stock(conn, ticker, name=name, sector="", market="")


def scrape_bs_worker(
    tickers: list[str],
    pool: object,
    *,
    years: int = 1,
    interval: float = 3.0,
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Process a chunk of tickers, storing results in the DB.

    Designed to run inside a ``ThreadPoolExecutor``.  Each worker opens
    its own DB connection and uses its own proxy sub-pool.
    """
    from formula_screening.datasources.irbank_common import scrape_worker

    def _process(ticker: str, html: str) -> list[dict]:
        return build_bs_rows(ticker, html, years=years)

    scrape_worker(
        tickers,
        pool,
        source="irbank_bs",
        process_fn=_process,
        on_html_fn=_on_bs_html,
        fetch_path="bs",
        validate_fn=_validate_bs_html,
        interval=interval,
        force=force,
        stats=stats,
        stats_lock=stats_lock,
        total=total,
        counter=counter,
    )
