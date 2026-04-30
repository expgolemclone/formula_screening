"""Shared screen-result column helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkCell:
    """A screen-result cell that should render as a hyperlink."""

    label: str
    url: str

    def __str__(self) -> str:
        return self.label


ScreenColumnValue = str | LinkCell
ScreenColumn = tuple[str, ScreenColumnValue]


_MONEX_URL_TEMPLATE = "https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd={ticker}&x=0&y=0"
_SIKIHO_URL_TEMPLATE = "https://shikiho.toyokeizai.net/stocks/{ticker}/shikiho"


def build_monex_url(ticker: str) -> str:
    """Return the Monex URL for a ticker."""

    return _MONEX_URL_TEMPLATE.format(ticker=ticker)


def build_sikiho_url(ticker: str) -> str:
    """Return the Shikiho URL for a ticker."""

    return _SIKIHO_URL_TEMPLATE.format(ticker=ticker)


def build_common_link_columns(stock: dict) -> list[ScreenColumn]:
    """Return the shared outbound-link columns for a stock."""

    ticker_value = stock.get("ticker")
    if ticker_value is None:
        return []

    ticker = str(ticker_value)
    return [
        ("monex", LinkCell(label="monex", url=build_monex_url(ticker))),
        ("sikiho", LinkCell(label="sikiho", url=build_sikiho_url(ticker))),
    ]


def merge_screen_columns(*groups: list[ScreenColumn]) -> list[ScreenColumn]:
    """Merge column groups while keeping the first occurrence of each header."""

    merged: list[ScreenColumn] = []
    seen: set[str] = set()
    for group in groups:
        for header, value in group:
            if header in seen:
                continue
            seen.add(header)
            merged.append((header, value))
    return merged
