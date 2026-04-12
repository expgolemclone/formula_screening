"""Scrape shares_outstanding from kabutan individual stock pages.

kabutan embeds the figure inside a table row of the form::

    <th scope='row'>発行済株式数</th>
    <td>20,000,000&nbsp;株</td>

Unlike IR BANK bulk JSON, this value is always the current post-split count,
so it survives stock splits that happen after the latest fiscal-year file.

kabutan serves the full page to plain HTTPS clients with a standard User-Agent,
so we skip the BrowserService (puppeteer-real-browser) machinery that the
IR BANK scrapers need for Cloudflare-protected pages.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, TypedDict

import requests

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from formula_screening.stealth import ProxyPool

logger: logging.Logger = logging.getLogger("formula_screening.kabutan_shares")

_KABUTAN_URL_TEMPLATE: str = "https://kabutan.jp/stock/?code={ticker}"
_USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
_HTTP_HEADERS: dict[str, str] = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "ja,en;q=0.9",
    "Accept-Encoding": "gzip",
}
_MAX_RETRIES: int = MAGIC["scrape"]["max_retries"]
_RETRY_DELAY: float = MAGIC["scrape"]["retry_delay"]


class SharesRow(TypedDict):
    """Result of :func:`build_shares_row`."""

    ticker: str
    shares_outstanding: int


_SHARES_PATTERN: re.Pattern[str] = re.compile(
    r"<th[^>]*>\s*発行済株式数\s*</th>\s*<td[^>]*>\s*([\d,]+)(?:&nbsp;|&#160;|\s)*株",
)


def parse_shares_outstanding(html: str) -> int | None:
    """Return the ``発行済株式数`` value from a kabutan stock page.

    Returns ``None`` when the label is missing or the value is non-numeric
    (e.g. ``-`` placeholder on a suspended stock).
    """
    match = _SHARES_PATTERN.search(html)
    if match is None:
        return None
    digits = match.group(1).replace(",", "")
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def build_shares_row(ticker: str, html: str) -> SharesRow | None:
    """Package a parsed value into a row ready for DB upsert.

    Returns ``None`` when ``parse_shares_outstanding`` cannot extract a number,
    so callers can branch on missing data without a second lookup.
    """
    shares = parse_shares_outstanding(html)
    if shares is None:
        return None
    return SharesRow(ticker=ticker, shares_outstanding=shares)


_STOCK_PAGE_TITLE_MARKER: str = "株の基本情報｜株探（かぶたん）"


def _is_kabutan_stock_page(ticker: str, html: str) -> bool:
    """Return True when *html* is the real kabutan stock page for *ticker*."""
    return (
        _STOCK_PAGE_TITLE_MARKER in html
        and f"stock/?code={ticker}" in html
    )


def _proxy_mapping(pool: ProxyPool) -> dict[str, str] | None:
    """Convert the next proxy from *pool* into a requests-compatible mapping."""
    if pool.direct:
        return None
    addr: str | None = pool.get()
    if addr is None:
        from formula_screening.stealth import ProxyUnavailableError

        raise ProxyUnavailableError("Proxy pool exhausted during kabutan fetch")
    proxy_url: str = f"http://{addr}"
    return {"http": proxy_url, "https": proxy_url}


def fetch_kabutan_html(
    ticker: str,
    pool: ProxyPool,
    *,
    timeout: float = 20.0,
) -> str | None:
    """Fetch the kabutan stock page HTML for *ticker* via plain HTTPS + retry.

    Returns ``None`` when every retry fails or when kabutan returns a page
    that does not contain the ``発行済株式数`` label (e.g. unknown ticker,
    bot-detection interstitial).
    """
    url: str = _KABUTAN_URL_TEMPLATE.format(ticker=ticker)

    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            time.sleep(_RETRY_DELAY)
        try:
            resp: requests.Response = requests.get(
                url,
                headers=_HTTP_HEADERS,
                proxies=_proxy_mapping(pool),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.warning(
                "kabutan fetch error for %s (attempt %d): %s",
                ticker, attempt + 1, exc,
            )
            if not pool.direct:
                pool.rotate()
            continue

        if resp.status_code == 404:
            logger.info("kabutan stock page not found for %s", ticker)
            return None

        if resp.status_code == 200 and _is_kabutan_stock_page(ticker, resp.text):
            return resp.text

        logger.warning(
            "kabutan blocked/unexpected for %s (status=%d, attempt %d)",
            ticker, resp.status_code, attempt + 1,
        )
        if not pool.direct:
            pool.rotate()

    return None
