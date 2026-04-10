"""Tests for BrowserService proxy field construction."""

from __future__ import annotations

from formula_screening.browser import _build_proxy_fields


class TestBuildProxyFields:

    def test_none_returns_empty_dict(self) -> None:
        result = _build_proxy_fields(None)

        assert result == {}

    def test_plain_http_proxy(self) -> None:
        result = _build_proxy_fields("http://1.2.3.4:8080")

        assert result == {"proxy": "1.2.3.4:8080"}

    def test_http_proxy_with_auth(self) -> None:
        result = _build_proxy_fields("http://alice:secret@1.2.3.4:8080")

        assert result == {
            "proxy": "1.2.3.4:8080",
            "proxyUsername": "alice",
            "proxyPassword": "secret",
        }

    def test_socks5_proxy(self) -> None:
        result = _build_proxy_fields("socks5h://1.2.3.4:1080")

        assert result == {"proxy": "1.2.3.4:1080", "proxyType": "socks5"}

    def test_socks5_proxy_with_auth(self) -> None:
        result = _build_proxy_fields("socks5://bob:pw@1.2.3.4:1080")

        assert result == {
            "proxy": "1.2.3.4:1080",
            "proxyType": "socks5",
            "proxyUsername": "bob",
            "proxyPassword": "pw",
        }

    def test_auth_with_special_chars_in_password(self) -> None:
        result = _build_proxy_fields("http://u:p%40ss@host:9000")

        assert result["proxyUsername"] == "u"
        assert result["proxyPassword"] == "p@ss"
        assert result["proxy"] == "host:9000"
