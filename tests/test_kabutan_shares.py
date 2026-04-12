"""Tests for the kabutan shares scraper."""

from __future__ import annotations

from pathlib import Path

import pytest

from formula_screening.scrape.kabutan_shares import (
    build_shares_row,
    fetch_kabutan_html,
    parse_shares_outstanding,
)
from formula_screening.stealth import ProxyPool

_FIXTURES: Path = Path(__file__).parent / "fixtures"


class _DummyResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_shares_outstanding_small_cap() -> None:
    html = _load("kabutan_8046.html")

    shares = parse_shares_outstanding(html)

    assert shares == 20_000_000


def test_parse_shares_outstanding_large_cap() -> None:
    html = _load("kabutan_7203.html")

    shares = parse_shares_outstanding(html)

    assert shares == 15_794_987_460


def test_parse_shares_outstanding_missing_label_returns_none() -> None:
    html = "<html><body><p>no such label here</p></body></html>"

    shares = parse_shares_outstanding(html)

    assert shares is None


def test_parse_shares_outstanding_handles_plain_space_separator() -> None:
    html = (
        "<html><body><table><tr>"
        "<th scope='row'>発行済株式数</th>"
        "<td>1,234,567 株</td>"
        "</tr></table></body></html>"
    )

    shares = parse_shares_outstanding(html)

    assert shares == 1_234_567


def test_parse_shares_outstanding_rejects_non_numeric() -> None:
    html = (
        "<html><body><table><tr>"
        "<th scope='row'>発行済株式数</th>"
        "<td>-&nbsp;株</td>"
        "</tr></table></body></html>"
    )

    shares = parse_shares_outstanding(html)

    assert shares is None


def test_build_shares_row_returns_ticker_and_shares() -> None:
    html = _load("kabutan_8046.html")

    row = build_shares_row("8046", html)

    assert row == {"ticker": "8046", "shares_outstanding": 20_000_000}


def test_build_shares_row_returns_none_when_unparseable() -> None:
    html = "<html><body>blank</body></html>"

    row = build_shares_row("9999", html)

    assert row is None


def test_fetch_kabutan_html_returns_valid_stock_page_without_shares_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        "<html><head>"
        "<title>ＫＨＣ【1451】株の基本情報｜株探（かぶたん）</title>"
        '<meta property="og:url" content="https://kabutan.jp/stock/?code=1451" />'
        "</head><body>missing shares row</body></html>"
    )
    calls = 0

    def fake_get(*args: object, **kwargs: object) -> _DummyResponse:
        del args, kwargs
        nonlocal calls
        calls += 1
        return _DummyResponse(200, html)

    monkeypatch.setattr("formula_screening.scrape.kabutan_shares.requests.get", fake_get)

    fetched_html = fetch_kabutan_html("1451", ProxyPool([], direct=True))

    assert fetched_html == html
    assert calls == 1


def test_fetch_kabutan_html_does_not_retry_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_get(*args: object, **kwargs: object) -> _DummyResponse:
        del args, kwargs
        nonlocal calls
        calls += 1
        return _DummyResponse(404, "<html><title>お探しのページが見つかりません。</title></html>")

    monkeypatch.setattr("formula_screening.scrape.kabutan_shares.requests.get", fake_get)

    fetched_html = fetch_kabutan_html("289A", ProxyPool([], direct=True))

    assert fetched_html is None
    assert calls == 1
