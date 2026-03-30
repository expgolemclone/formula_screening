"""Parse forecast (会社予想) data from IR BANK /results pages.

The ``/results`` page contains static HTML tables.  The first table
("会社業績") has columns:

    年度 | 売上 | 営利 | 経常 | 当期利益 | 包括 | EPS | ROE | ROA | 営利率 | 原価率 | 販管費率

Forecast rows are identified by a "予" suffix on the period label
(e.g. "2026/03予").  Monetary values use Japanese unit suffixes
(億/万) while EPS is in yen and ratios carry a ``%`` suffix.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("formula_screening.irbank_forecast")

# --- Column mapping (header text → DB item_name) ----------------------------

_COLUMN_MAP: dict[str, str] = {
    "売上": "revenue",
    "営利": "operating_income",
    "経常": "ordinary_income",
    "当期利益": "net_income",
    "包括": "comprehensive_income",
    "EPS": "basic_eps",
    "ROE": "roe",
    "ROA": "roa",
    "営利率": "operating_margin",
    "原価率": "cost_ratio",
    "販管費率": "sga_ratio",
}

# --- Japanese number parsing -------------------------------------------------

# Matches patterns like "287億", "19.6億", "3億7214万", "7214万", "−0.49億"
_JP_NUMBER_RE = re.compile(
    r"(?P<sign>[−\-])?(?P<oku>[\d,.]+)億(?:(?P<man>[\d,.]+)万)?$"
    r"|"
    r"(?P<sign2>[−\-])?(?P<man_only>[\d,.]+)万$"
)

_OKU = 100_000_000  # 億
_MAN = 10_000  # 万


def _strip_commas(s: str) -> str:
    return s.replace(",", "")


def parse_jp_number(text: str) -> float | None:
    """Parse a Japanese-formatted number string to float.

    Handles: ``"287億"``, ``"19.6億"``, ``"3億7214万"``,
    ``"7214万"``, ``"−0.49億"``, ``"55.82"``, ``"7.19%"``,
    ``"26.71円"``, ``"赤字"``, ``"—"``, ``"−"``.

    Returns None for missing / unparseable values.
    """
    text = text.strip()
    if not text or text in ("—", "−", "-", "赤字"):
        return None

    # Strip trailing units that don't affect magnitude
    text = text.rstrip("円%")

    # Try Japanese magnitude suffixes (億 / 万)
    m = _JP_NUMBER_RE.match(text)
    if m:
        if m.group("oku") is not None:
            sign = -1 if m.group("sign") else 1
            value = float(_strip_commas(m.group("oku"))) * _OKU
            if m.group("man"):
                value += float(_strip_commas(m.group("man"))) * _MAN
            return sign * value
        # man-only branch
        sign = -1 if m.group("sign2") else 1
        return sign * float(_strip_commas(m.group("man_only"))) * _MAN

    # Plain number (e.g. "55.82", "-3.5")
    try:
        return float(_strip_commas(text))
    except ValueError:
        logger.debug("Unparseable value: %r", text)
        return None


# --- HTML table parser -------------------------------------------------------

_FORECAST_PERIOD_RE = re.compile(r"(\d{4}/\d{2})予")

# Monetary columns where 億/万 unit suffixes are expected.
_MONETARY_ITEMS = frozenset({
    "revenue", "operating_income", "ordinary_income",
    "net_income", "comprehensive_income",
})

# Ratio columns stored as percentages (strip % suffix, keep numeric).
_RATIO_ITEMS = frozenset({
    "roe", "roa", "operating_margin", "cost_ratio", "sga_ratio",
})


def _extract_table_after_heading(html: str, heading: str) -> str | None:
    """Return the first ``<table>...</table>`` block after *heading*."""
    idx = html.find(heading)
    if idx == -1:
        return None
    table_start = html.find("<table", idx)
    if table_start == -1:
        return None
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        return None
    return html[table_start:table_end + len("</table>")]


def _parse_rows(table_html: str) -> list[list[str]]:
    """Extract rows of cell text from a ``<table>`` HTML fragment.

    Handles both ``<th>`` (header) and ``<td>`` (data) cells.
    """
    rows: list[list[str]] = []
    for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr_match.group(1), re.DOTALL)
        if not cells:
            continue
        rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return rows


def parse_forecast_table(html: str) -> list[dict]:
    """Parse the 会社業績 table and return forecast ("予") rows.

    Returns a list of dicts: ``{period, item_name, value}``.
    """
    table_html = _extract_table_after_heading(html, "会社業績")
    if table_html is None:
        logger.warning("会社業績 table not found")
        return []

    rows = _parse_rows(table_html)
    if len(rows) < 2:
        return []

    # First row is the header
    header = rows[0]
    # Map column index → item_name
    col_map: list[tuple[int, str]] = []
    for i, col_name in enumerate(header):
        item_name = _COLUMN_MAP.get(col_name)
        if item_name is not None:
            col_map.append((i, item_name))

    items: list[dict] = []
    for row in rows[1:]:
        if not row:
            continue
        period_cell = row[0]
        m = _FORECAST_PERIOD_RE.search(period_cell)
        if m is None:
            continue  # not a forecast row

        period = m.group(1).replace("/", "-")

        for col_idx, item_name in col_map:
            if col_idx >= len(row):
                continue
            value = parse_jp_number(row[col_idx])
            if value is None:
                continue
            items.append({
                "period": period,
                "item_name": item_name,
                "value": value,
            })

    return items


# --- Row builder for DB upsert -----------------------------------------------


def build_forecast_rows(ticker: str, html: str) -> list[dict]:
    """Parse forecast data and return rows ready for DB upsert.

    Returns a list of dicts with keys:
        ticker, period, statement, item_name, value, source
    """
    items = parse_forecast_table(html)
    return [
        {
            "ticker": ticker,
            "period": item["period"],
            "statement": "forecast",
            "item_name": item["item_name"],
            "value": item["value"],
            "source": "irbank_forecast",
        }
        for item in items
    ]


# --- HTML validation ----------------------------------------------------------


def validate_results_html(html: str) -> bool:
    """Return True if the HTML looks like a valid /results page."""
    return "会社業績" in html


# --- Parallel worker ----------------------------------------------------------


def scrape_forecast_worker(
    tickers: list[str],
    pool: object,
    *,
    interval: float = 3.0,
    force: bool = False,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    total: int,
    counter: list[int],
) -> None:
    """Process a chunk of tickers, storing forecast results in the DB."""
    from formula_screening.datasources.irbank_common import scrape_worker

    scrape_worker(
        tickers,
        pool,
        source="irbank_forecast",
        process_fn=build_forecast_rows,
        fetch_path="results",
        validate_fn=validate_results_html,
        interval=interval,
        force=force,
        stats=stats,
        stats_lock=stats_lock,
        total=total,
        counter=counter,
    )
