"""Fetch stock prices from Stooq daily ASCII text file."""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from formula_screening.browser import BrowserService

logger: logging.Logger = logging.getLogger("formula_screening.stooq_price")

_STOOQ_CAPTCHA_URL: str = "https://stooq.com/db/"
_STOOQ_DOWNLOAD_URL: str = "https://stooq.com/db/d/?d={date}&t=d"
_JP_TICKER_RE: re.Pattern[str] = re.compile(r"^(\d{4,5})\.JP$", re.IGNORECASE)
_DAILY_TXT_RE: re.Pattern[str] = re.compile(r"^\d{8}_d\.txt$")
_DATE_FMT: re.Pattern[str] = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

_CAPTCHA_TIMEOUT: int = 120_000
_DOWNLOAD_TIMEOUT: int = 120_000


class StooqPriceRow(TypedDict):
    price: float
    date: str


def download_daily_txt(browser: BrowserService, download_dir: str) -> Path:
    """Download the daily ASCII text file from Stooq.

    Navigates to the Stooq DB page first to solve CAPTCHA,
    then downloads the daily file for the previous trading day.
    """
    from formula_screening.browser import BrowserServiceError

    resp = browser.fetch(_STOOQ_CAPTCHA_URL, timeout=_CAPTCHA_TIMEOUT)
    if resp.error is not None:
        raise BrowserServiceError(f"Failed to load Stooq CAPTCHA page: {resp.error}")
    logger.info("CAPTCHA page loaded, proceeding to download")

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    url = _STOOQ_DOWNLOAD_URL.format(date=today)

    file_path: str = browser.download(
        url,
        download_dir,
        timeout=_DOWNLOAD_TIMEOUT,
    )
    logger.info("Downloaded Stooq daily txt: %s", file_path)
    return Path(file_path)


def find_latest_daily_txt(directory: Path) -> Path | None:
    """Return the most recent ``YYYYMMDD_d.txt`` file in *directory*, or None."""
    candidates = sorted(
        (p for p in directory.iterdir() if _DAILY_TXT_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def parse_daily_txt(
    txt_path: Path,
    *,
    tickers: set[str],
) -> dict[str, StooqPriceRow]:
    """Extract close prices for Japanese stocks from a Stooq daily text file.

    Only tickers present in the *tickers* filter set are returned.
    Stooq uses ``XXXX.JP`` format; this maps to DB ticker ``XXXX``.
    """
    results: dict[str, StooqPriceRow] = {}

    with txt_path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)

        for row in reader:
            if len(row) < 8:
                continue

            m = _JP_TICKER_RE.match(row[0])
            if m is None:
                continue

            ticker: str = m.group(1)
            if ticker not in tickers:
                continue

            raw_close: str = row[7].strip()
            raw_date: str = row[2].strip()
            if not raw_close or not raw_date:
                continue

            results[ticker] = StooqPriceRow(
                price=float(raw_close),
                date=_format_date(raw_date),
            )

    return results


def _format_date(raw: str) -> str:
    """Convert ``YYYYMMDD`` to ``YYYY-MM-DD``."""
    m = _DATE_FMT.match(raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return raw
