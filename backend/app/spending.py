"""Pure transaction classification and monthly spending calculations.

Plaid amounts are positive for outflows. Unclassified records never silently
enter a budget total. Manual classifications are stored independently.
"""

from decimal import Decimal, InvalidOperation


def visible_transactions(transactions):
    posted_pending_ids = {t.get("pending_transaction_id") for t in transactions
                          if not t.get("pending") and t.get("pending_transaction_id")}
    return [t for t in transactions if not (t.get("pending") and t.get("transaction_id") in posted_pending_ids)]


def classification(transaction, overrides):
    # A posted record inherits the pending override unless explicitly edited.
    return (overrides.get(transaction.get("transaction_id"))
            or overrides.get(transaction.get("pending_transaction_id")) or {})


def calculate_month(transactions, overrides, month):
    totals = {}
    unclassified = 0
    for transaction in visible_transactions(transactions):
        date = transaction.get("date", "")
        if not isinstance(date, str) or not date.startswith(month + "-"):
            continue
        choice = classification(transaction, overrides)
        kind, category_id = choice.get("kind"), choice.get("category_id")
        if transaction.get("iso_currency_code") not in (None, "USD") or transaction.get("unofficial_currency_code"):
            unclassified += 1
            continue
        if not kind:
            unclassified += 1
            continue
        if kind not in ("spending", "refund") or not category_id:
            continue
        try:
            amount = Decimal(str(transaction["amount"]))
            if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("Invalid transaction amount")
        except (KeyError, InvalidOperation):
            raise ValueError("Invalid transaction amount") from None
        # A refund is applied in its own transaction month. Enforce direction
        # so a mistaken classification cannot silently reverse the total.
        if (kind == "spending" and amount < 0) or (kind == "refund" and amount > 0):
            unclassified += 1
            continue
        totals[category_id] = totals.get(category_id, 0) + int(amount * 100)
    return totals, unclassified
