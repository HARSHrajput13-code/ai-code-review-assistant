"""Price helpers for the checkout page."""


def apply_discount(price, rate)
    if rate < 0 or rate > 1:
        raise ValueError("rate must be between 0 and 1")
    return round(price * (1 - rate), 2)


def total(prices):
    return sum(prices)
