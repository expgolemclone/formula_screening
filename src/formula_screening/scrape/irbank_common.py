"""IR BANK URL builder — retry/proxy plumbing lives in ``http_fetch``."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC
from formula_screening.scrape.http_fetch import fetch_html

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

_IRBANK_URL_TEMPLATE: str = "https://irbank.net/{ticker}/{path}"


def fetch_irbank_html(
    ticker: str,
    path: str,
    pool: ProxyPool,
    *,
    validate_fn: Callable[[str], bool],
    browser: BrowserService,
    timeout: int = MAGIC["browser"]["page_timeout"],
) -> str | None:
    """Fetch an IR BANK page (e.g. ``/bs``, ``/results``) via the shared retry loop."""
    url: str = _IRBANK_URL_TEMPLATE.format(ticker=ticker, path=path)
    return fetch_html(
        url,
        pool,
        validate_fn=validate_fn,
        browser=browser,
        timeout=timeout,
        label=f"{ticker}/{path}",
    )
