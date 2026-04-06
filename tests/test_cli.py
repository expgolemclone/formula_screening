"""Tests for CLI helpers."""

from __future__ import annotations

import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from formula_screening.cli import _cmd_clear_failure_cache, _cmd_probe_proxies, _cmd_refresh
from formula_screening.stealth import ProxyUnavailableError


class TestProbeProxies:
    """Tests for the proxy probe command."""

    def test_clears_legacy_cache_and_uses_minimal_defaults(self, capsys) -> None:
        args = Namespace(
            clear_legacy_cache=True,
            proxy=None,
            target_proxies=1,
            check_sites=0,
        )
        pool = MagicMock()
        pool.size = 2
        pool.get.return_value = "http://2.2.2.2:80"

        with (
            patch("formula_screening.stealth.clear_failure_cache", return_value=(5, 10)) as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto", return_value=pool) as auto_mock,
        ):
            _cmd_probe_proxies(args)

        out = capsys.readouterr().out
        clear_mock.assert_called_once_with(reasons={"legacy"})
        auto_mock.assert_called_once_with(target_count=1, quality_check_count=0)
        assert "Removed 5 legacy failure-cache entries (10 remaining)." in out
        assert "Live proxies ready: 2" in out
        assert "Current proxy: http://2.2.2.2:80" in out

    def test_uses_explicit_proxy_without_auto_fetch(self, capsys) -> None:
        args = Namespace(
            clear_legacy_cache=False,
            proxy="http://9.9.9.9:8080",
            target_proxies=1,
            check_sites=0,
        )
        pool = MagicMock()
        pool.size = 1
        pool.get.return_value = "http://9.9.9.9:8080"

        with (
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_url", return_value=pool) as from_url_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto") as auto_mock,
        ):
            _cmd_probe_proxies(args)

        out = capsys.readouterr().out
        clear_mock.assert_not_called()
        from_url_mock.assert_called_once_with("http://9.9.9.9:8080")
        auto_mock.assert_not_called()
        assert "Live proxies ready: 1" in out


class TestClearFailureCache:
    """Tests for the failure-cache management command."""

    def test_shows_distribution_without_clearing_when_no_reason_is_given(self, capsys) -> None:
        args = Namespace(all=False, reason=None)

        with (
            patch(
                "formula_screening.stealth.failure_cache_reason_counts",
                return_value={"not_a_proxy": 2, "quality_failed": 1},
            ) as counts_mock,
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
        ):
            _cmd_clear_failure_cache(args)

        out = capsys.readouterr().out
        assert counts_mock.call_count == 1
        clear_mock.assert_not_called()
        assert "Failure cache before: 3 (not_a_proxy=2, quality_failed=1)" in out
        assert "Nothing cleared. Pass --reason REASON (repeatable) or --all." in out

    def test_clears_only_requested_reasons_and_prints_before_after(self, capsys) -> None:
        args = Namespace(all=False, reason=["quality_failed", "anon_unreachable"])

        with (
            patch(
                "formula_screening.stealth.failure_cache_reason_counts",
                side_effect=[
                    {"anon_unreachable": 2, "not_a_proxy": 4, "quality_failed": 3},
                    {"not_a_proxy": 4},
                ],
            ) as counts_mock,
            patch(
                "formula_screening.stealth.clear_failure_cache",
                return_value=(5, 4),
            ) as clear_mock,
        ):
            _cmd_clear_failure_cache(args)

        out = capsys.readouterr().out
        assert counts_mock.call_count == 2
        clear_mock.assert_called_once_with(reasons={"quality_failed", "anon_unreachable"})
        assert "Failure cache before: 9 (anon_unreachable=2, not_a_proxy=4, quality_failed=3)" in out
        assert "Removed 5 entries." in out
        assert "Failure cache after: 4 (not_a_proxy=4)" in out


class TestRefresh:
    """Tests for refresh CLI wiring."""

    def test_passes_workers_to_refresh_stale_sources(self) -> None:
        args = Namespace(
            force=False,
            workers=100,
            verbose=False,
            proxy=None,
            target_proxies=1,
            check_sites=1,
        )
        pool = object()

        with (
            patch("formula_screening.cli._resolve_proxy_pool", return_value=pool),
            patch(
                "formula_screening.cache_invalidation.check_and_invalidate",
                return_value=["irbank_bs.py"],
            ),
            patch("formula_screening.cache_invalidation.refresh_stale_sources") as refresh_mock,
            patch("formula_screening.cache_invalidation.save_hashes") as save_mock,
            patch(
                "formula_screening.cache_invalidation.compute_hashes",
                return_value={"irbank_bs.py": "hash"},
            ),
        ):
            _cmd_refresh(args)

        refresh_mock.assert_called_once_with(["irbank_bs.py"], proxy_pool=pool, workers=100)
        save_mock.assert_called_once_with({"irbank_bs.py": "hash"})


class TestMain:
    """Tests for CLI top-level error handling."""

    def test_exits_with_abort_message_on_proxy_error(self, capsys) -> None:
        from formula_screening.cli import main

        with (
            patch.object(sys, "argv", ["formula_screening", "probe-proxies"]),
            patch("formula_screening.cli.setup_logging"),
            patch("formula_screening.cli.init_db"),
            patch(
                "formula_screening.cli._cmd_probe_proxies",
                side_effect=ProxyUnavailableError("validation_fail_rate=51.0%"),
            ),
        ):
            with pytest.raises(SystemExit, match="1"):
                main()

        err = capsys.readouterr().err
        assert "ABORT: validation_fail_rate=51.0%" in err
