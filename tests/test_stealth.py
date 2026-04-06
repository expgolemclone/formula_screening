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
    _prefilter_proxy,
    _save_failure_cache,
    _source_label,
    _tcp_reachable,
    ProxyPool,
    ProxyUnavailableError,
    clear_failure_cache,
    failure_cache_reason_counts,
    failure_cache_reasons,
    fetch_live_proxies,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# _source_label
# ---------------------------------------------------------------------------


class TestSourceLabel:
    """Tests for proxy source URL label extraction."""

    def test_extracts_github_username(self) -> None:
        url: str = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"

        assert _source_label(url) == "TheSpeedX"

    def test_extracts_jsdelivr_github_username(self) -> None:
        url: str = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt"

        assert _source_label(url) == "proxifly"

    def test_extracts_github_pages_username(self) -> None:
        url: str = "https://vakhov.github.io/fresh-proxy-list/http.txt"

        assert _source_label(url) == "vakhov"

    def test_extracts_proxyscrape_api_label(self) -> None:
        url: str = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http"

        assert _source_label(url) == "proxyscrape_api"

    def test_fallback_for_non_github_url(self) -> None:
        url: str = "https://example.com/proxies.txt"

        assert _source_label(url) == url


# ---------------------------------------------------------------------------
# _tcp_reachable
# ---------------------------------------------------------------------------


class TestTcpReachable:
    """Tests for the TCP connect pre-filter."""

    def test_returns_true_for_open_port(self) -> None:
        with patch("formula_screening.stealth.socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            assert _tcp_reachable("1.2.3.4:8080", timeout=0.5) is True

    def test_returns_false_for_closed_port(self) -> None:
        with patch(
            "formula_screening.stealth.socket.create_connection",
            side_effect=OSError("Connection refused"),
        ):
            assert _tcp_reachable("1.2.3.4:8080", timeout=0.5) is False

    def test_returns_false_for_invalid_addr(self) -> None:
        assert _tcp_reachable("not-a-valid-addr", timeout=0.1) is False


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
            result: str = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result == "ok"

    @patch("formula_screening.stealth._VALIDATION_SITES", ["a.com", "b.com", "c.com"])
    def test_fails_when_anon_leaks(self) -> None:
        def fake_get(url: str, **kwargs: object) -> MagicMock:
            resp: MagicMock = MagicMock()
            resp.status_code = 200
            if "httpbin" in url:
                resp.json.return_value = {"headers": {"X-Forwarded-For": "1.2.3.4"}}
            return resp

        with patch("formula_screening.stealth.requests.get", side_effect=fake_get):
            result: str = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result == "anon_leak"

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
            result: str = _check_proxy("1.2.3.4:8080", quality_check_count=2)

        assert result == "quality_failed"

    def test_fails_when_proxy_is_not_really_a_proxy(self) -> None:
        import requests

        with patch(
            "formula_screening.stealth.requests.get",
            side_effect=requests.exceptions.ProxyError("Tunnel connection failed: 400 Bad Request"),
        ):
            result: str = _check_proxy("1.2.3.4:8080", quality_check_count=0)

        assert result == "not_a_proxy"


# ---------------------------------------------------------------------------
# _prefilter_proxy
# ---------------------------------------------------------------------------


class TestPrefilterProxy:
    """Tests for the fast proxy pre-filter."""

    def test_returns_tcp_unreachable_before_proxy_checks(self) -> None:
        with patch("formula_screening.stealth._tcp_reachable", return_value=False):
            assert _prefilter_proxy("1.2.3.4:8080") == "tcp_unreachable"


# ---------------------------------------------------------------------------
# Failure cache
# ---------------------------------------------------------------------------


class TestFailureCache:
    """Tests for proxy failure cache load/save."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache: dict[str, dict[str, float | str]] = {
            "1.2.3.4:80": {"reason": "not_a_proxy", "ts": now},
            "5.6.7.8:3128": {"reason": "anon_unreachable", "ts": now},
        }

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            _save_failure_cache(cache)
            loaded = _load_failure_cache()

        assert loaded == cache

    def test_expired_entries_are_discarded(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        old_ts: float = now - 2 * 3600
        cache = {
            "old:80": {"reason": "anon_unreachable", "ts": old_ts},
            "fresh:80": {"reason": "not_a_proxy", "ts": old_ts},
        }

        cache_file.write_text(json.dumps(cache))

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded = _load_failure_cache()

        assert "old:80" not in loaded
        assert loaded["fresh:80"]["reason"] == "not_a_proxy"

    def test_legacy_entries_are_loaded_with_short_ttl(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache_file.write_text(json.dumps({"legacy:80": now}))

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded = _load_failure_cache()

        assert loaded == {"legacy:80": {"reason": "legacy", "ts": now}}

    def test_clear_failure_cache_removes_only_requested_reasons(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache = {
            "legacy:80": {"reason": "legacy", "ts": now},
            "new:80": {"reason": "not_a_proxy", "ts": now},
        }
        cache_file.write_text(json.dumps(cache))

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            removed, remaining = clear_failure_cache(reasons={"legacy"})
            loaded = _load_failure_cache()

        assert removed == 1
        assert remaining == 1
        assert "legacy:80" not in loaded
        assert loaded["new:80"]["reason"] == "not_a_proxy"

    def test_failure_cache_reason_counts_reports_active_distribution(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache = {
            "a:80": {"reason": "not_a_proxy", "ts": now},
            "b:80": {"reason": "quality_failed", "ts": now},
            "c:80": {"reason": "quality_failed", "ts": now},
        }
        cache_file.write_text(json.dumps(cache))

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            counts = failure_cache_reason_counts()

        assert counts == {"not_a_proxy": 1, "quality_failed": 2}

    def test_failure_cache_reasons_matches_known_reason_keys(self) -> None:
        assert failure_cache_reasons() == [
            "anon_leak",
            "anon_unreachable",
            "legacy",
            "not_a_proxy",
            "quality_failed",
            "tcp_unreachable",
        ]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "nonexistent.json"

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded: dict[str, float] = _load_failure_cache()

        assert loaded == {}

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        cache_file.write_text("not valid json{{{")

        with patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file):
            loaded = _load_failure_cache()

        assert loaded == {}


# ---------------------------------------------------------------------------
# fetch_live_proxies — failure cache integration
# ---------------------------------------------------------------------------


class TestFetchLiveProxiesCache:
    """Tests that fetch_live_proxies filters and records failures."""

    def test_skips_cached_failures(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        now: float = time.time()
        cache_file.write_text(json.dumps({
            "1.1.1.1:80": {"reason": "not_a_proxy", "ts": now},
        }))

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    ["1.1.1.1:80", "2.2.2.2:80"],
                    {"src": 2},
                    {"1.1.1.1:80": "src", "2.2.2.2:80": "src"},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="ok"),
            patch("formula_screening.stealth._check_proxy", return_value="ok"),
        ):
            result: list[str] = fetch_live_proxies(
                target_count=1, check_workers=2, quality_check_count=1,
            )

        assert "2.2.2.2:80" in result
        assert "1.1.1.1:80" not in result

    def test_records_new_failures(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    ["9.9.9.9:80"],
                    {"src": 1},
                    {"9.9.9.9:80": "src"},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="ok"),
            patch("formula_screening.stealth._check_proxy", return_value="anon_leak"),
        ):
            result: list[str] = fetch_live_proxies(
                target_count=1, check_workers=2, quality_check_count=1,
            )

        assert result == []
        saved = json.loads(cache_file.read_text())
        assert saved["9.9.9.9:80"]["reason"] == "anon_leak"

    def test_prefilter_failures_are_cached_with_reason(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    ["10.0.0.1:80"],
                    {"src": 1},
                    {"10.0.0.1:80": "src"},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="not_a_proxy"),
        ):
            result: list[str] = fetch_live_proxies(
                target_count=1, check_workers=2, quality_check_count=1,
            )

        assert result == []
        saved = json.loads(cache_file.read_text())
        assert saved["10.0.0.1:80"]["reason"] == "not_a_proxy"

    def test_does_not_abort_before_100_validation_checks(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        candidates: list[str] = [f"10.0.0.{idx}:80" for idx in range(1, 100)]

        def fake_check(addr: str, *, quality_check_count: int) -> str:
            last_octet = int(addr.split(".")[-1].split(":")[0])
            if last_octet <= 60:
                return "anon_leak"
            return "ok"

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    candidates,
                    {"src": len(candidates)},
                    {addr: "src" for addr in candidates},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="ok"),
            patch("formula_screening.stealth._check_proxy", side_effect=fake_check),
        ):
            result = fetch_live_proxies(
                target_count=1000,
                check_workers=1,
                quality_check_count=1,
            )

        assert len(result) == 39

    def test_does_not_abort_at_exactly_50_percent_failure_rate(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        candidates: list[str] = [f"10.0.1.{idx}:80" for idx in range(1, 101)]

        def fake_check(addr: str, *, quality_check_count: int) -> str:
            last_octet = int(addr.split(".")[-1].split(":")[0])
            if last_octet <= 50:
                return "anon_leak"
            return "ok"

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    candidates,
                    {"src": len(candidates)},
                    {addr: "src" for addr in candidates},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="ok"),
            patch("formula_screening.stealth._check_proxy", side_effect=fake_check),
        ):
            result = fetch_live_proxies(
                target_count=1000,
                check_workers=1,
                quality_check_count=1,
            )

        assert len(result) == 50

    def test_aborts_when_failure_rate_exceeds_50_percent_after_100_checks(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / ".proxy_failures.json"
        candidates: list[str] = [f"10.0.2.{idx}:80" for idx in range(1, 121)]

        def fake_check(addr: str, *, quality_check_count: int) -> str:
            last_octet = int(addr.split(".")[-1].split(":")[0])
            if last_octet <= 51:
                return "anon_leak"
            return "ok"

        with (
            patch("formula_screening.stealth.PROXY_FAILURE_CACHE", cache_file),
            patch(
                "formula_screening.stealth._fetch_proxy_candidates",
                return_value=(
                    candidates,
                    {"src": len(candidates)},
                    {addr: "src" for addr in candidates},
                ),
            ),
            patch("formula_screening.stealth._prefilter_proxy", return_value="ok"),
            patch("formula_screening.stealth._check_proxy", side_effect=fake_check),
        ):
            with pytest.raises(
                ProxyUnavailableError,
                match="validation_fail_rate=51.0% \\(>50.0%, min_checked=100\\)",
            ):
                fetch_live_proxies(
                    target_count=1000,
                    check_workers=1,
                    quality_check_count=1,
                )

        saved = json.loads(cache_file.read_text())
        assert len(saved) == 51
        assert saved["10.0.2.1:80"]["reason"] == "anon_leak"


class TestProxyPool:
    """Tests for user-facing proxy acquisition errors."""

    def test_from_auto_includes_diagnostics(self) -> None:
        with patch(
            "formula_screening.stealth.fetch_live_proxies",
            side_effect=lambda **_: [],
        ), patch(
            "formula_screening.stealth._LAST_PROXY_FAILURE_SUMMARY",
            "0/10 passed; validation [not_a_proxy=10]",
        ):
            with pytest.raises(ProxyUnavailableError, match="validation \\[not_a_proxy=10\\]"):
                ProxyPool.from_auto(target_count=1, quality_check_count=1)
