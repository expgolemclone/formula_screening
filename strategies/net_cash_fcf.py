"""清原達郎式 ネットキャッシュ比率 + FCFイールド スクリーニング.

net_cash_ratio > 1.0
0 < per < 10
自己資本比率 > 50%
過去5年間の平均FCFイールド (FCF / 時価総額) > 0

FCF = operating_cf + investing_cf
"""

from __future__ import annotations

from formula_screening.config import MAGIC

_FCF_YEARS: int = MAGIC["screening"]["fcf_years"]


def _fcf_yield_avg(stock: dict) -> float | None:
    """過去*_FCF_YEARS*年分の平均FCFイールドを返す."""
    market_cap = stock.get("metrics", {}).get("market_cap")
    if not market_cap or market_cap <= 0:
        return None

    cf_history: list[tuple[str, dict]] = stock.get("cf_history", [])
    if not cf_history:
        return None

    yields: list[float] = []
    for _period, cf in cf_history[:_FCF_YEARS]:
        operating_cf = cf.get("operating_cf")
        investing_cf = cf.get("investing_cf")
        free_cf = cf.get("free_cf")
        fcf = (
            free_cf
            if free_cf is not None
            else (
                (operating_cf + investing_cf)
                if operating_cf is not None and investing_cf is not None
                else None
            )
        )
        if fcf is not None:
            yields.append(fcf / market_cap)

    if not yields:
        return None
    return sum(yields) / len(yields)


def screen(stock: dict) -> bool:
    m = stock.get("metrics", {})
    ratio = m.get("net_cash_ratio")
    per = m.get("per")
    equity_ratio = m.get("equity_ratio")
    if ratio is None or per is None or equity_ratio is None:
        return False
    if not (ratio > 1.0 and 0 < per < 10 and equity_ratio > 50):
        return False

    avg = _fcf_yield_avg(stock)
    if avg is None:
        return False
    return avg > 0


def sort_key(stock: dict) -> float:
    """ソートキー: 平均FCFイールド降順."""
    avg = _fcf_yield_avg(stock)
    return avg if avg is not None else float("-inf")


def columns(stock: dict) -> list[tuple[str, str]]:
    """戦略固有の表示カラムを返す."""
    avg = _fcf_yield_avg(stock)
    return [("FCF_Y%", f"{avg * 100:.2f}" if avg is not None else "-")]
