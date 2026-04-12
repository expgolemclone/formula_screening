"""Tests for CLI helpers."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from formula_screening.cli import (
    _build_markdown_table,
    _cmd_clear_failure_cache,
    _cmd_probe_proxies,
    _cmd_screen,
    _render_markdown_with_glow,
    _resolve_proxy_pool,
    _write_csv,
    dispatch_workers,
)
from formula_screening.screen_output import LinkCell
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


class TestResolveProxyPool:
    """Tests for proxy-pool resolution and transient cache handling."""

    def test_clears_transient_failure_cache_for_full_auto_fetch_prices_run(self) -> None:
        args = Namespace(
            command="fetch-prices",
            proxy="auto",
            proxy_file=None,
            ticker=None,
            target_proxies=1,
            check_sites=0,
        )
        pool = MagicMock()

        with (
            patch(
                "formula_screening.stealth.clear_failure_cache",
                return_value=(3, 4),
            ) as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto", return_value=pool) as auto_mock,
        ):
            result = _resolve_proxy_pool(args)

        assert result is pool
        clear_mock.assert_called_once_with(reasons={"tcp_unreachable", "anon_unreachable"})
        auto_mock.assert_called_once_with(target_count=1, quality_check_count=0)

    def test_does_not_clear_transient_failure_cache_for_targeted_auto_fetch_prices_run(self) -> None:
        args = Namespace(
            command="fetch-prices",
            proxy="auto",
            proxy_file=None,
            ticker=["7203"],
            target_proxies=1,
            check_sites=0,
        )
        pool = MagicMock()

        with (
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto", return_value=pool) as auto_mock,
        ):
            result = _resolve_proxy_pool(args)

        assert result is pool
        clear_mock.assert_not_called()
        auto_mock.assert_called_once_with(target_count=1, quality_check_count=0)

    def test_does_not_clear_transient_failure_cache_for_explicit_proxy(self) -> None:
        args = Namespace(
            command="refresh",
            proxy="http://9.9.9.9:8080",
            proxy_file=None,
            ticker=None,
            target_proxies=1,
            check_sites=0,
        )
        pool = MagicMock()

        with (
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_url", return_value=pool) as from_url_mock,
        ):
            result = _resolve_proxy_pool(args)

        assert result is pool
        clear_mock.assert_not_called()
        from_url_mock.assert_called_once_with("http://9.9.9.9:8080")

    def test_proxy_direct_returns_empty_direct_pool_without_cache_clear(self) -> None:
        args = Namespace(
            command="scrape-forecast",
            proxy="direct",
            proxy_file=None,
            ticker=None,
            target_proxies=1,
            check_sites=0,
        )

        with (
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto") as auto_mock,
            patch("formula_screening.stealth.ProxyPool.from_url") as from_url_mock,
        ):
            result = _resolve_proxy_pool(args)

        assert result.size == 0
        assert result.direct is True
        clear_mock.assert_not_called()
        auto_mock.assert_not_called()
        from_url_mock.assert_not_called()

    def test_unspecified_proxy_defaults_to_direct_pool(self) -> None:
        args = Namespace(
            command="scrape-forecast",
            proxy="direct",
            proxy_file=None,
            ticker=None,
            target_proxies=1,
            check_sites=0,
        )

        with (
            patch("formula_screening.stealth.clear_failure_cache") as clear_mock,
            patch("formula_screening.stealth.ProxyPool.from_auto") as auto_mock,
            patch("formula_screening.stealth.ProxyPool.from_url") as from_url_mock,
        ):
            result = _resolve_proxy_pool(args)

        assert result.size == 0
        assert result.direct is True
        clear_mock.assert_not_called()
        auto_mock.assert_not_called()
        from_url_mock.assert_not_called()


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


class TestDispatchWorkers:
    """Tests for worker-level fatal proxy errors."""

    def test_reraises_proxy_unavailable_error_from_worker(self) -> None:
        pool = MagicMock()
        pool.size = 1
        pool.split.return_value = [MagicMock()]

        def worker_fn(*args, **kwargs) -> None:
            raise ProxyUnavailableError("Proxy pool exhausted during request execution")

        with pytest.raises(ProxyUnavailableError, match="Proxy pool exhausted"):
            dispatch_workers(
                ["7203"],
                pool,
                worker_fn=worker_fn,
                label="prices",
                workers=1,
            )


class TestCmdScreenRequiredSources:
    """_cmd_screen must forward strategy REQUIRED_SOURCES into ensure_data_available."""

    def test_passes_strategy_required_sources_to_bootstrap(self, tmp_path: Path) -> None:
        # Arrange
        strategy: Path = tmp_path / "with_req.py"
        strategy.write_text(
            'REQUIRED_SOURCES = ["irbank", "prices"]\n'
            'FILTERS = [("per", ">", 0)]\n'
        )
        args = Namespace(
            strategy=str(strategy),
            output=None,
            open=None,
            workers=1,
            proxy=None,
            proxy_file=None,
            target_proxies=0,
            check_sites=0,
            command="screen",
        )

        captured: dict[str, object] = {}

        def _fake_ensure(**kwargs: object) -> None:
            captured.update(kwargs)

        with (
            patch("formula_screening.bootstrap.ensure_data_available", side_effect=_fake_ensure),
            patch("formula_screening.screener.run_screening", return_value=[]),
            patch("formula_screening.cli.get_connection", return_value=MagicMock()),
        ):
            # Act
            _cmd_screen(args)

        # Assert
        assert captured.get("required_sources") == ["irbank", "prices"]

    def test_passes_all_sources_when_strategy_omits_required_sources(self, tmp_path: Path) -> None:
        # Arrange
        from formula_screening.bootstrap import DATA_SOURCES

        strategy: Path = tmp_path / "no_req.py"
        strategy.write_text('FILTERS = [("per", ">", 0)]\n')
        args = Namespace(
            strategy=str(strategy),
            output=None,
            open=None,
            workers=1,
            proxy="direct",
            proxy_file=None,
            target_proxies=0,
            check_sites=0,
            command="screen",
        )

        captured: dict[str, object] = {}

        def _fake_ensure(**kwargs: object) -> None:
            captured.update(kwargs)

        with (
            patch("formula_screening.bootstrap.ensure_data_available", side_effect=_fake_ensure),
            patch("formula_screening.screener.run_screening", return_value=[]),
            patch("formula_screening.cli.get_connection", return_value=MagicMock()),
        ):
            # Act
            _cmd_screen(args)

        # Assert
        assert captured["required_sources"] == DATA_SOURCES


class TestMarkdownOutput:
    """Tests for Markdown + glow based screen output."""

    def test_build_markdown_table_renders_markdown_links(self) -> None:
        # Arrange
        hits = [
            {
                "ticker": "7203",
                "name": "トヨタ自動車",
                "price": 2500.0,
                "metrics": {
                    "net_cash_ratio": 1.23,
                    "per": 8.5,
                    "pbr": 1.01,
                    "dividend_yield": 2.34,
                },
            }
        ]

        def _extra_cols(_: dict) -> list[tuple[str, str | LinkCell]]:
            return [
                (
                    "monex",
                    LinkCell(
                        label="monex",
                        url="https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd=7203&x=0&y=0",
                    ),
                ),
                (
                    "sikiho",
                    LinkCell(
                        label="sikiho",
                        url="https://shikiho.toyokeizai.net/stocks/7203/shikiho",
                    ),
                ),
            ]

        # Act
        markdown = _build_markdown_table(hits, extra_cols_fn=_extra_cols)

        # Assert
        assert "[monex](https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd=7203&x=0&y=0)" in markdown
        assert "[sikiho](https://shikiho.toyokeizai.net/stocks/7203/shikiho)" in markdown
        assert "| Ticker | Name | Price | NC_Ratio | PER | PBR | Div% | monex | sikiho |" in markdown

    def test_render_markdown_with_glow_uses_glow_when_available(self) -> None:
        # Arrange
        markdown = "| A |\n| - |\n| x |\n"

        with (
            patch("formula_screening.cli.shutil.which", return_value="/usr/bin/glow") as which_mock,
            patch("formula_screening.cli.subprocess.run") as run_mock,
        ):
            # Act
            rendered = _render_markdown_with_glow(markdown)

        # Assert
        assert rendered is True
        which_mock.assert_called_once_with("glow")
        run_mock.assert_called_once_with(
            ["/usr/bin/glow", "-"],
            check=True,
            input=markdown,
            text=True,
        )

    def test_write_csv_writes_raw_urls_for_link_cells(self, tmp_path: Path) -> None:
        # Arrange
        path = tmp_path / "result.csv"
        hits = [
            {
                "ticker": "7203",
                "name": "トヨタ自動車",
                "price": 2500.0,
                "metrics": {
                    "net_cash_ratio": 1.23,
                    "per": 8.5,
                    "pbr": 1.01,
                    "dividend_yield": 2.34,
                },
            }
        ]

        def _extra_cols(_: dict) -> list[tuple[str, str | LinkCell]]:
            return [
                (
                    "monex",
                    LinkCell(
                        label="monex",
                        url="https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd=7203&x=0&y=0",
                    ),
                ),
                (
                    "sikiho",
                    LinkCell(
                        label="sikiho",
                        url="https://shikiho.toyokeizai.net/stocks/7203/shikiho",
                    ),
                ),
            ]

        # Act
        _write_csv(hits, path, extra_cols_fn=_extra_cols)

        # Assert
        written = path.read_text(encoding="utf-8")
        assert "monex,sikiho" in written
        assert "https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd=7203&x=0&y=0" in written
        assert "https://shikiho.toyokeizai.net/stocks/7203/shikiho" in written
