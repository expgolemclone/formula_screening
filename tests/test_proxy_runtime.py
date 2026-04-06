"""Tests for fatal proxy exhaustion propagation in datasource workers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from formula_screening.datasources.irbank_common import fetch_irbank_html
from formula_screening.datasources.yfinance_price import _fetch_one
from formula_screening.stealth import ProxyPool, ProxyUnavailableError


class TestYfinanceProxyExhaustion:
    """Tests for fatal proxy errors in yfinance access."""

    def test_fetch_one_reraises_proxy_unavailable_error(self) -> None:
        with patch(
            "formula_screening.datasources.yfinance_price.create_session",
            side_effect=ProxyUnavailableError("Proxy pool exhausted during request execution"),
        ):
            with pytest.raises(ProxyUnavailableError, match="Proxy pool exhausted"):
                _fetch_one("7203", ProxyPool(["1.1.1.1:80"]))


class TestIrbankProxyExhaustion:
    """Tests for fatal proxy errors in IR BANK access."""

    def test_fetch_irbank_html_reraises_proxy_unavailable_error(self) -> None:
        validate_fn = MagicMock(return_value=True)

        with patch(
            "formula_screening.stealth.create_session",
            side_effect=ProxyUnavailableError("Proxy pool exhausted during request execution"),
        ):
            with pytest.raises(ProxyUnavailableError, match="Proxy pool exhausted"):
                fetch_irbank_html("7203", "bs", ProxyPool(["1.1.1.1:80"]), validate_fn=validate_fn)
