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
    """過去*_FCF_YEARS*年分の平均FCFイールドを返す.

    NOTE: 過去のFCFを現在の時価総額で割るため、ルックアヘッドバイアスが含まれる。
    バックテストには不適だが、直近のスクリーニング用途では実用的な近似値となる。
    """
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


def _croic(stock: dict) -> float | None:
    """CROIC: FCF / 投下資本 (stockholders_equity + interest_bearing_debt)."""
    cf: dict = stock.get("cf", {})
    bs: dict = stock.get("bs", {})

    free_cf: float | None = cf.get("free_cf")
    if free_cf is None:
        operating_cf: float | None = cf.get("operating_cf")
        investing_cf: float | None = cf.get("investing_cf")
        if operating_cf is not None and investing_cf is not None:
            free_cf = operating_cf + investing_cf

    if free_cf is None:
        return None

    stockholders_equity: float | None = bs.get("stockholders_equity")
    if stockholders_equity is None:
        return None

    short_term_debt: float | None = bs.get("short_term_debt")
    long_term_debt: float | None = bs.get("long_term_debt")
    interest_bearing_debt: float = (short_term_debt or 0) + (long_term_debt or 0)
    invested_capital: float = stockholders_equity + interest_bearing_debt

    if invested_capital <= 0:
        return None
    return free_cf / invested_capital


def sort_key(stock: dict) -> float:
    """ソートキー: 平均FCFイールド降順."""
    avg = _fcf_yield_avg(stock)
    return avg if avg is not None else float("-inf")


def columns(stock: dict) -> list[tuple[str, str]]:
    """戦略固有の表示カラムを返す."""
    avg = _fcf_yield_avg(stock)
    croic = _croic(stock)
    return [
        ("FCF_Y%", f"{avg * 100:.2f}" if avg is not None else "-"),
        ("CROIC%", f"{croic * 100:.2f}" if croic is not None else "-"),
    ]
