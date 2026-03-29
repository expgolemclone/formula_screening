"""清原達郎式 ネットキャッシュ比率スクリーニング.

簡易版: ネットキャッシュ = 現金同等物 − 負債合計
        ネットキャッシュ比率 = ネットキャッシュ / 時価総額

本来の式: 流動資産 + 投資有価証券×70% − 負債
IR BANKに流動資産・投資有価証券がないため現金同等物で代用（保守的）。

比率が1以上 = 「会社がただで買えるほど割安」（清原氏の定義）。
"""


def screen(stock: dict) -> bool:
    m = stock.get("metrics", {})
    ratio = m.get("net_cash_ratio")
    if ratio is None:
        return False
    return ratio >= 1.0
