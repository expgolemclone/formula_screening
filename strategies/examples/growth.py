"""Growth screen: high ROE + high operating margin."""


def screen(stock: dict) -> bool:
    m = stock.get("metrics", {})
    roe = m.get("roe")
    margin = m.get("operating_margin")
    if roe is None or margin is None:
        return False
    return roe > 15 and margin > 15
