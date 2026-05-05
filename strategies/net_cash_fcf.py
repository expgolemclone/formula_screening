"""清原達郎式 ネットキャッシュ比率 + FCFイールド スクリーニング.

net_cash_ratio >= -1.0
0 < per < 10
自己資本比率 > 50%
過去N年間の平均FCFイールド (FCF / 時価総額) > 0
"""

from __future__ import annotations

from collections.abc import Callable

from formula_screening.indicators import croic, fcf_yield_avg

REQUIRED_SOURCES: list[str] = ["irbank", "irbank_bs", "prices"]

FILTERS: list[
    tuple[str | Callable[[dict], float | None], str, float | tuple[float, float]]
] = [
    ("net_cash_ratio", ">=", -1.0),
    ("per", "between", (0, 10)),
    ("equity_ratio", ">", 50),
    (fcf_yield_avg, ">", 0),
]

SORT: str = "net_cash_ratio"

COLUMNS: list[tuple[str, Callable[[dict], float | None], str]] = [
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
]


if __name__ == "__main__":
    import sys

    from formula_screening.cli import main as _cli_main

    sys.argv = ["formula_screening", "screen", "-s", __file__, *sys.argv[1:]]
    _cli_main()
