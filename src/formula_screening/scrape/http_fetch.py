"""Generic HTML fetch helper with proxy rotation and retry.

This module holds the shared retry / proxy-rotation loop used by every
page scraper (IR BANK, kabutan, ...). Site-specific modules only need to
construct the URL and provide a ``validate_fn`` that recognises a usable
response body.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from formula_screening.config import MAGIC

if TYPE_CHECKING:
    from formula_screening.browser import BrowserService
    from formula_screening.stealth import ProxyPool

logger: logging.Logger = logging.getLogger("formula_screening.http_fetch")

_MAX_RETRIES: int = MAGIC["scrape"]["max_retries"]
_PROXY_REMOVE_ON_ERROR: bool = MAGIC["scrape"]["proxy_remove_on_error"]
_RETRY_DELAY: float = MAGIC["scrape"]["retry_delay"]


def fetch_html(
    url: str,
    pool: ProxyPool,
    *,
    validate_fn: Callable[[str], bool],
    browser: BrowserService,
    timeout: int = MAGIC["browser"]["page_timeout"],
    label: str | None = None,
) -> str | None:
    """Fetch *url* via :class:`BrowserService`, returning HTML if acceptable.

    The function walks the retry loop with proxy rotation; each attempt is
    accepted only when the HTTP status is 200 *and* ``validate_fn(html)``
    returns ``True``. Returns ``None`` when every retry is exhausted.

    *label* is an optional short tag used in log messages — defaults to the
    URL itself, which can be noisy for scrapers that fetch the same domain
    with only a differing ticker/path.
    """
    from formula_screening.browser import BrowserResponse, BrowserServiceError
    from formula_screening.stealth import ProxyUnavailableError, random_delay

    log_label: str = label or url
    direct_mode: bool = pool.direct

    def _handle_proxy_error() -> None:
        if direct_mode:
            return
        if _PROXY_REMOVE_ON_ERROR:
            pool.report_failure()
        else:
            pool.rotate()

    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            time.sleep(_RETRY_DELAY)
        if direct_mode:
            proxy_url: str | None = None
        else:
            proxy_url = pool.get()
            if proxy_url is None:
                raise ProxyUnavailableError("Proxy pool exhausted during request execution")

        try:
            resp: BrowserResponse = browser.fetch(url, proxy=proxy_url, timeout=timeout)
        except BrowserServiceError as exc:
            logger.warning(
                "Browser service error for %s (attempt %d): %s",
                log_label, attempt + 1, exc,
            )
            _handle_proxy_error()
            continue

        if resp.status == 200 and resp.html is not None and validate_fn(resp.html):
            return resp.html

        if resp.error is not None:
            logger.warning(
                "Fetch error for %s (attempt %d): %s",
                log_label, attempt + 1, resp.error,
            )
            _handle_proxy_error()
            continue

        if resp.html is not None and not validate_fn(resp.html):
            snippet: str = resp.html[:500].replace("\n", " ")
            logger.warning(
                "Blocked for %s (status=%d, attempt %d): %s",
                log_label, resp.status, attempt + 1, snippet,
            )
            _handle_proxy_error()
            random_delay(
                MAGIC["scrape"]["rate_limit_delay_min"],
                MAGIC["scrape"]["rate_limit_delay_max"],
            )
            continue

        logger.warning(
            "Unexpected status %d for %s (attempt %d)",
            resp.status, log_label, attempt + 1,
        )
        return None

    return None
