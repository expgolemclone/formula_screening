"""Display-width-aware string formatting for East Asian characters."""

from __future__ import annotations

import unicodedata


def display_width(s: str) -> int:
    """Return the terminal display width of *s*.

    East Asian Wide / Fullwidth characters count as 2; all others as 1.
    """
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def ljust(s: str, width: int) -> str:
    """Left-justify *s* to *width* columns (display-width-aware)."""
    pad = width - display_width(s)
    return s + " " * max(pad, 0)


def truncate(s: str, width: int) -> str:
    """Truncate *s* to fit within *width* display columns."""
    w = 0
    for i, ch in enumerate(s):
        eaw = unicodedata.east_asian_width(ch)
        cw = 2 if eaw in ("W", "F") else 1
        if w + cw > width:
            return s[:i]
        w += cw
    return s
