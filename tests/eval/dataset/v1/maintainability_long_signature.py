"""Invoice creation."""


def create_invoice(customer_id, amount, currency, due_date, tax_rate, discount, notes, reference):
    total = amount * (1 + tax_rate) - discount
    return {
        "customer": customer_id,
        "total": round(total, 2),
        "currency": currency,
        "due": due_date,
        "notes": notes,
        "reference": reference,
    }
