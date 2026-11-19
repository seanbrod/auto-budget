from app.spending import calculate_month, classification, visible_transactions


def test_pending_to_posted_and_manual_override():
    pending = {"transaction_id": "pending", "pending": True, "date": "2026-11-01", "amount": 10}
    posted = {"transaction_id": "posted", "pending": False, "pending_transaction_id": "pending",
              "date": "2026-11-02", "amount": 12}
    overrides = {"pending": {"kind": "spending", "category_id": "food"}}
    assert visible_transactions([pending, posted]) == [posted]
    assert calculate_month([pending, posted], overrides, "2026-11") == ({"food": 1200}, 0)
    overrides["posted"] = {"kind": "excluded"}
    assert classification(posted, overrides) == {"kind": "excluded"}
    assert calculate_month([pending, posted], overrides, "2026-11") == ({}, 0)


def test_refunds_apply_in_received_month_and_unknowns_are_visible():
    txns = [
        {"transaction_id": "a", "date": "2026-10-30", "amount": 20},
        {"transaction_id": "b", "date": "2026-11-02", "amount": -5},
        {"transaction_id": "c", "date": "2026-11-03", "amount": 30},
        {"transaction_id": "d", "date": "2026-11-04", "amount": 100},
    ]
    choices = {"a": {"kind": "spending", "category_id": "food"},
               "b": {"kind": "refund", "category_id": "food"}, "d": {"kind": "card_payment"}}
    assert calculate_month(txns, choices, "2026-10") == ({"food": 2000}, 0)
    assert calculate_month(txns, choices, "2026-11") == ({"food": -500}, 1)


def test_wrong_direction_does_not_silently_distort_spending():
    txns = [{"transaction_id": "a", "date": "2026-11-01", "amount": -10}]
    assert calculate_month(txns, {"a": {"kind": "spending", "category_id": "food"}}, "2026-11") == ({}, 1)


def test_non_usd_is_not_added_to_dollar_spending():
    txns = [{"transaction_id": "a", "date": "2026-11-01", "amount": 10, "iso_currency_code": "EUR"}]
    assert calculate_month(txns, {"a": {"kind": "spending", "category_id": "food"}}, "2026-11") == ({}, 1)
