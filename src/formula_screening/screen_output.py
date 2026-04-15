"""Shared screen-result column helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkCell:
    """A screen-result cell that should render as a hyperlink."""

    label: str
    url: str


logger = logging.getLogger(__name__)

    def __str__(self) -> str:
        return self.label


ScreenColumnValue = str | LinkCell
ScreenColumn = tuple[str, ScreenColumnValue]


_MONEX_URL_TEMPLATE = "https://monex.ifis.co.jp/index.php?sa=find&ta=e&wd={ticker}&x=0&y=0"
_SIKIHO_URL_TEMPLATE = "https://shikiho.toyokeizai.net/stocks/{ticker}/shikiho"
_OSC8_ESCAPE = "\033]8;;"
_OSC8_TERMINATOR = "\033\\"


def build_monex_url(ticker: str) -> str:
    """Return the Monex URL for a ticker."""

    return _MONEX_URL_TEMPLATE.format(ticker=ticker)


def build_sikiho_url(ticker: str) -> str:
    """Return the Shikiho URL for a ticker."""

    return _SIKIHO_URL_TEMPLATE.format(ticker=ticker)


def build_osc8_hyperlink(label: str, url: str) -> str:
    """Return *label* wrapped in an OSC 8 hyperlink."""

    return f"{_OSC8_ESCAPE}{url}{_OSC8_TERMINATOR}{label}{_OSC8_ESCAPE}{_OSC8_TERMINATOR}"


def supports_osc8_hyperlinks(env: Mapping[str, str], is_tty: bool) -> bool:
    """Return True when the terminal environment likely supports OSC 8."""

    if not is_tty:
        return False

    term = env.get("TERM", "")
    if term == "dumb":
        return False

    if env.get("KITTY_WINDOW_ID") or term == "xterm-kitty":
        return True

    if env.get("WT_SESSION") or env.get("KONSOLE_VERSION"):
        return True

    vte_version = env.get("VTE_VERSION")
    if vte_version is not None:
        try:
            if int(vte_version) >= 5000:
                return True
        except ValueError:
            logger.debug("Non-numeric VTE_VERSION: %s", vte_version)

    term_program = env.get("TERM_PROGRAM", "").casefold()
    return term_program in {"iterm.app", "wezterm", "vscode"}


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
