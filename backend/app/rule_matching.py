"""Bounded rule matching; no Plaid category information is used."""

from decimal import Decimal, InvalidOperation
from functools import lru_cache

import re2


@lru_cache(maxsize=32)
def compile_pattern(value):
    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False
    options.max_mem = 1_048_576
    return re2.compile(value, options=options)


def normalized(value):
    return " ".join(value.casefold().split())


def matches(rule, transaction):
    if not rule.get("enabled"):
        return False
    field = transaction.get(rule["field"])
    if not isinstance(field, str):
        return False
    text, value = normalized(field), rule["value"]
    if rule["operator"] == "exact" and text != value:
        return False
    if rule["operator"] == "contains" and value not in text:
        return False
    if rule["operator"] == "regex" and not compile_pattern(rule["value"]).search(text):
        return False
    if rule.get("account_id") and rule["account_id"] != transaction.get("account_id"):
        return False
    try:
        amount = Decimal(str(transaction["amount"]))
        if not amount.is_finite():
            return False
    except (KeyError, InvalidOperation):
        return False
    return not ((rule["direction"] == "outgoing" and amount <= 0)
                or (rule["direction"] == "incoming" and amount >= 0))


def winning_rule(rules, transaction, *, new_only=False):
    for rule in sorted(rules, key=lambda r: (r["priority"], r["id"])):
        if new_only:
            first_seen = transaction.get("first_seen_ns", 0)
            if first_seen <= rule["created_ns"] or (rule.get("ended_ns") and first_seen > rule["ended_ns"]):
                continue
        candidate = transaction.get("_rule_match_data", transaction) if new_only else transaction
        if matches(rule, candidate):
            return rule
    return None
