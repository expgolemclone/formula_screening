"""Tests for stealth proxy validation and failure cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from formula_screening.stealth import (
    _check_proxy,
    _hit_anon,
    _hit_quality,
    _load_failure_cache,
    _save_failure_cache,
    fetch_live_proxies,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# _hit_anon
# ---------------------------------------------------------------------------


class TestHitAnon:
    """Tests for the anonymity-check helper."""

    def test_anonymous_proxy_returns_true(self) -> None:
        resp: MagicMock = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"headers": {"Host": "httpbin.io"}}

        with patch("formula_screening.stealth.requests.get", return_value=resp):
            result: bool | None = _hit_anon(
                "https://httpbin.io/headers", {"http": "x", "https": "x"}, {}, 3,
            )

        assert result is True

    def test_leaky_proxy_returns_false(self) -> None:
        resp: MagicMock = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"headers": {"X-Forwarded-For": "1.2.3.4"}}

        with patch("formula_screening.stealth.requests.get", return_value=resp):
            result: bool | None = _hit_anon(
                "https://httpbin.io/headers", {"http": "x", "https": "x"}, {}, 3,
            )

        assert result is False

    def test_unreachable_endpoint_returns_none(self) -> None:
        import requests

        with patch(
            "formula_screening.stealth.requests.get",
            side_effect=requests.ConnectionError,
        ):
            result: bool | None = _hit_anon(
                "https://httpbin.io/headers", {"http": "x", "https": "x"}, {}, 3,
            )

        assert result is None

    def test_non_200_returns_none(self) -> None:
        resp: MagicMock = MagicMock()
        resp.status_code = 503

        with patch("formula_screening.stealth.requests.get", return_value=resp):
            result: bool | None = _hit_anon(
                "https://httpbin.io/headers", {"http": "x", "https": "x"}, {}, 3,
            )

        assert result is None


# ---------------------------------------------------------------------------
# _hit_quality
# ---------------------------------------------------------------------------


class TestHitQuality:
    """Tests for the quality-check helper."""

    def test_reachable_site_returns_true(self) -> None:
        resp: MagicMock = MagicMock()
        resp.status_code = 200

        with patch("formula_screening.stealth.requests.get", return_value=resp):
            assert _hit_quality("example.com", {"http": "x", "https": "x"}, {}, 5) is True

    def test_unreachable_site_returns_false(self) -> None:
        import requests

        with patch(
            "formula_screening.stealth.requests.get",
            side_effect=requests.ConnectionError,
        ):
            assert _hit_quality("example.com", {"http": "x", "https": "x"}, {}, 5) is False


# ---------------------------------------------------------------------------
# _check_proxy (fully parallel)
# ---------------------------------------------------------------------------


class TestCheckProxy:
    """Tests for the combined parallel proxy checker."""

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_passes_when_anon_and_quality_ok(self) -> None:
        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp: MagicMock = MagicMock()
            resp.status_code = 200
            if "httpbin" in url:
                resp.json.return_value = {"headers": {}}
            return resp

        with patch("formula_screening.stealth.requests.get", side_effect=fake_get):
            result: str | None = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result == "1.2.3.4:8080"

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_fails_when_anon_leaks(self) -> None:
        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp: MagicMock = MagicMock()
            resp.status_code = 200
            if "httpbin" in url:
                resp.json.return_value = {"headers": {"X-Forwarded-For": "1.2.3.4"}}
            return resp

        with patch("formula_screening.stealth.requests.get", side_effect=fake_get):
            result: str | None = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result is None

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_fails_when_quality_fails(self) -> None:
        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp: MagicMock = MagicMock()
            if "httpbin" in url:
                resp.status_code = 200
                resp.json.return_value = {"headers": {}}
            else:
                resp.status_code = 403
            return resp

        with patch("formula_screening.stealth.requests.get", side_effect=fake_get):
            result: str | None = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result is None


# ---------------------------------------------------------------------------
# Failure cache
# ---------------------------------------------------------------------------


class TestFailureCache:
    """Tests for proxy failure cache load/save."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache: dict[str, float] = {"1.2.3.4:80": now, "5.6.7.8:3128": now}

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            _save_failure_cache(cache)
            loaded: dict[str, float] = _load_failure_cache()

        assert loaded == cache

    def test_expired_entries_are_discarded(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        old_ts: float = now - 25 * 3600  # 25 hours ago (TTL=24h)
        cache: dict[str, float] = {"old:80": old_ts, "fresh:80": now}

        cache_file.write_text(json.dumps(cache))

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded: dict[str, float] = _load_failure_cache()

        assert "old:80" not in loaded
        assert "fresh:80" in loaded

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "nonexistent.json"

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded: dict[str, float] = _load_failure_cache()

        assert loaded == {}

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        cache_file.write_text("not valid json{{{")

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded: dict[str, float] = _load_failure_cache()

        assert loaded == {}


# ---------------------------------------------------------------------------
# fetch_live_proxies — failure cache integration
# ---------------------------------------------------------------------------


class TestFetchLiveProxiesCache:
    """Tests that fetch_live_proxies filters and records failures."""

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_skips_cached_failures(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache_file.write_text(json.dumps({"1.1.1.1:80": now}))

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp: MagicMock = MagicMock()
            resp.status_code = 200
            if "httpbin" in url:
                resp.json.return_value = {"headers": {}}
            elif "raw.githubusercontent" in url:
                resp.text = "1.1.1.1:80\n2.2.2.2:80\n"
            return resp

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch("formula_screening.stealth.requests.get", side_effect=fake_get),
        ):
            result: list[str] = fetch_live_proxies(
                target_count=1, check_workers=2, quality_check_count=1,
            )

        assert "2.2.2.2:80" in result
        assert "1.1.1.1:80" not in result

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_records_new_failures(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"

        call_count: dict[str, int] = {}

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            call_count[url] = call_count.get(url, 0) + 1
            resp: MagicMock = MagicMock()
            if "raw.githubusercontent" in url:
                resp.status_code = 200
                resp.text = "9.9.9.9:80\n"
                return resp
            if "httpbin" in url:
                resp.status_code = 200
                resp.json.return_value = {"headers": {"Via": "squid"}}
                return resp
            resp.status_code = 403
            return resp

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch("formula_screening.stealth.requests.get", side_effect=fake_get),
        ):
            result: list[str] = fetch_live_proxies(
                target_count=1, check_workers=2, quality_check_count=1,
            )

        assert result == []
        saved: dict[str, float] = json.loads(cache_file.read_text())
        assert "9.9.9.9:80" in saved
