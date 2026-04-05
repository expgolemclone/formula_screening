"""清原達郎式 ネットキャッシュ比率スクリーニング.

net_cash_ratio = 流動資産 - 棚卸資産 + 投資有価証券×70% − 負債
0 < per < 10
自己資本比率 > 50%
"""

FILTERS: list[tuple[str, str, float | tuple[float, float]]] = [
    ("net_cash_ratio", ">", 1.0),
    ("per", "between", (0, 10)),
    ("equity_ratio", ">", 50),
]
