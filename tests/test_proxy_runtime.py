"""Tests for fatal proxy exhaustion propagation in datasource workers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from formula_screening.browser import BrowserResponse, BrowserService
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


class TestIrbankDirectMode:
    """Direct-connection mode bypasses proxy rotation entirely."""

    def test_fetch_irbank_html_direct_mode_calls_browser_with_no_proxy(self) -> None:
        # Arrange
        validate_fn: MagicMock = MagicMock(return_value=True)
        pool: ProxyPool = ProxyPool([], direct=True)
        browser: MagicMock = MagicMock(spec=BrowserService)
        browser.fetch.return_value = BrowserResponse(html="<html>ok</html>", status=200, error=None)

        # Act
        result = fetch_irbank_html(
            "7203", "results", pool,
            validate_fn=validate_fn, browser=browser,
        )

        # Assert
        assert result == "<html>ok</html>"
        assert browser.fetch.call_count == 1
        _, kwargs = browser.fetch.call_args
        assert kwargs["proxy"] is None

    def test_fetch_irbank_html_direct_mode_retries_without_calling_report_failure(self) -> None:
        # Arrange
        validate_fn: MagicMock = MagicMock(return_value=True)
        pool: MagicMock = MagicMock(spec=ProxyPool)
        pool.direct = True
        browser: MagicMock = MagicMock(spec=BrowserService)
        browser.fetch.side_effect = [
            BrowserResponse(html=None, status=502, error="network"),
            BrowserResponse(html="<html>ok</html>", status=200, error=None),
        ]

        # Act
        result = fetch_irbank_html(
            "7203", "results", pool,
            validate_fn=validate_fn, browser=browser,
        )

        # Assert
        assert result == "<html>ok</html>"
        assert browser.fetch.call_count == 2
        pool.get.assert_not_called()
        pool.report_failure.assert_not_called()
        pool.rotate.assert_not_called()
