"""Remove duplicate order IDs while keeping their order."""


def unique_orders(order_ids):
    """Return order IDs without duplicates, in first-seen order."""
    seen = []
    result = []
    for order_id in order_ids:
        if order_id not in seen:
            seen.append(order_id)
            result.append(order_id)
    return result
