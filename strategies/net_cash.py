"""清原達郎式 ネットキャッシュ比率スクリーニング.

net_cash_ratio=流動資産-棚卸資産 + 投資有価証券×70% − 負債
per<=10
自己資本比率>=50%

"""


def screen(stock: dict) -> bool:
    m = stock.get("metrics", {})
    ratio = m.get("net_cash_ratio")
    per = m.get("per")
    equity_ratio = m.get("equity_ratio")
    if ratio is None or per is None or equity_ratio is None:
        return False
    return ratio >= 1.0 and per <= 10 and equity_ratio >= 50
