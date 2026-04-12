"""Browser service client — delegates to stock_db.browser.

Provides backward-compatible no-arg BrowserService() constructor
that reads config from formula_screening.config.MAGIC.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from stock_db.browser import (  # noqa: F401
    BrowserConfig,
    BrowserResponse,
    BrowserServiceError,
)
from stock_db.browser import BrowserService as _BrowserService
from stock_db.browser import build_proxy_fields as _build_proxy_fields

from formula_screening.config import MAGIC

_BROWSER_SERVICE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "browser_service"


class BrowserService(_BrowserService):
    def __init__(self) -> None:
        super().__init__(
            config=cast(BrowserConfig, MAGIC["browser"]),
            browser_service_dir=_BROWSER_SERVICE_DIR,
        )
