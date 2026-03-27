"""Value investing screen: low PER + decent ROE."""


def screen(stock: dict) -> bool:
    m = stock.get("metrics", {})
    per = m.get("per")
    roe = m.get("roe")
    if per is None or roe is None:
        return False
    return per < 15 and roe > 10
