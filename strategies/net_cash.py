"""清原達郎式 ネットキャッシュ比率スクリーニング.

net_cash = 流動資産 - 棚卸資産 + 投資有価証券×70% − (流動負債 + 固定負債)
net_cash_ratio = net_cash / 時価総額 > 1.0
0 < per < 10
自己資本比率 > 50%
"""

REQUIRED_SOURCES: list[str] = ["irbank", "irbank_bs", "prices"]

FILTERS: list[tuple[str, str, float | tuple[float, float]]] = [
    ("net_cash_ratio", ">", 1.0),
    ("per", "between", (0, 10)),
    ("equity_ratio", ">", 50),
]
