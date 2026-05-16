"""Helpers for the EDINET preferred-share flag."""

from __future__ import annotations


def preferred_share_flag(stock: dict) -> bool | None:
    """Return the canonical preferred-share flag stored under ``bs``."""

    value = stock.get("bs", {}).get("has_preferred_shares")
    if value is None:
        return None
    if isinstance(value, bool):
        msg = f"bs.has_preferred_shares must be 1.0, 0.0, or None: {value!r}"
        raise ValueError(msg)
    if value == 1.0:
        return True
    if value == 0.0:
        return False
    msg = f"bs.has_preferred_shares must be 1.0, 0.0, or None: {value!r}"
    raise ValueError(msg)


def preferred_share_label(stock: dict) -> str:
    """Return the preferred-share flag formatted for human-readable tables."""

    flag = preferred_share_flag(stock)
    if flag is None:
        return "-"
    return "あり" if flag else "なし"
