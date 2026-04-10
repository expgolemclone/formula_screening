"""Tests for fatal proxy exhaustion propagation in datasource workers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from formula_screening.browser import BrowserService
from formula_screening.scrape.irbank_common import fetch_irbank_html
from formula_screening.stealth import ProxyPool, ProxyUnavailableError


class TestIrbankProxyExhaustion:
    """Tests for fatal proxy errors in IR BANK access."""

    def test_fetch_irbank_html_reraises_proxy_unavailable_error(self) -> None:
        validate_fn: MagicMock = MagicMock(return_value=True)
        pool: ProxyPool = ProxyPool([])
        browser: MagicMock = MagicMock(spec=BrowserService)

        with pytest.raises(ProxyUnavailableError, match="Proxy pool exhausted"):
            fetch_irbank_html(
                "7203", "bs", pool,
                validate_fn=validate_fn, browser=browser,
            )
