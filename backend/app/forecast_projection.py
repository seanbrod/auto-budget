"""Estimated cash-flow projection using household-owned cached Sandbox balances."""

import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response

from app.forecast_api import versioned_occurrences
from app.forecast_matches import automatic_matches
from app.plaid_api import household, query_all
from app.plaid_worker import table
from app.spending import visible_transactions
from app.transactions_api import household_transactions

router = APIRouter()


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            return None
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        return None


def calculate_projection(today, accounts, incomplete, transactions, entries, matches):
    warnings = ["Balances may be cached; sync completion is not the balance's as-of time.",
                "Unmatched actual transactions may already be included in balances; review matches to avoid double counting."]
    balance = 0
    included = 0
    sync_times = []
    missing = incomplete
    for account in accounts:
        kind, subtype = account.get("type"), account.get("subtype")
        if (kind, subtype) not in (("depository", "checking"), ("depository", "savings"),
                                    ("credit", "credit card")):
            continue
        included += 1
        amounts = account.get("balances") or {}
        if amounts.get("iso_currency_code") not in (None, "USD") or amounts.get("unofficial_currency_code"):
            missing = True
            continue
        current = amounts.get("current")
        available = amounts.get("available")
        selected = available if kind == "depository" and available is not None else current
        value = cents(selected)
        if value is None or not account.get("sync_completed_at"):
            missing = True
            continue
        sync_times.append(int(account["sync_completed_at"]))
        if kind == "depository" and available is None:
            warnings.append("A cash account uses current balance because available balance is missing.")
        balance += value if kind == "depository" else -value
    if not included:
        missing = True
    visible = visible_transactions(transactions)
    matched_ids = {m["transaction_id"] for m in matches}
    visible_ids = {identifier for transaction in visible for identifier in
                   (transaction.get("transaction_id"), transaction.get("pending_transaction_id")) if identifier}
    if matched_ids - visible_ids:
        warnings.append("Some matched transactions are unavailable; their occurrences remain in the estimate.")
    fulfilled = {(m["entry_id"], m["occurrence_date"]) for m in matches if m["transaction_id"] in visible_ids}
    pending_ids = {identifier for transaction in visible if transaction.get("pending") for identifier in
                   (transaction.get("transaction_id"), transaction.get("pending_transaction_id")) if identifier}
    pending_fulfilled = {(m["entry_id"], m["occurrence_date"]) for m in matches if m["transaction_id"] in pending_ids}
    if any(t.get("pending") for t in visible):
        warnings.append("Pending activity may not be fully reflected in cached balances; no balance adjustment was made.")
    if missing:
        warnings.append("A required account balance or connection is unavailable; the total is withheld.")
    months = []
    first_index = today.year * 12 + today.month - 1
    for offset in range(12):
        year, month_index = divmod(first_index + offset, 12)
        month = month_index + 1
        start = max(today, date(year, month, 1))
        end = date(year, month, calendar.monthrange(year, month)[1])
        income = expense = matched_count = pending_count = 0
        for entry_id, row in entries.items():
            for day, entry in versioned_occurrences(row, start, end):
                if (entry_id, day.isoformat()) in fulfilled:
                    matched_count += 1
                    pending_count += (entry_id, day.isoformat()) in pending_fulfilled
                    continue
                if entry["kind"] == "income":
                    income += int(entry["amount_cents"])
                else:
                    expense += int(entry["amount_cents"])
        balance += income - expense
        months.append({"month": f"{year:04d}-{month:02d}", "income_cents": income,
                       "expense_cents": expense, "fulfilled_count": matched_count, "pending_fulfilled_count": pending_count,
                       "projected_cents": None if missing else balance})
    return {"as_of": today.isoformat(), "starting_cents": None if missing else balance - sum(
        month["income_cents"] - month["expense_cents"] for month in months),
            "oldest_sync_completed_at": min(sync_times) if sync_times else None,
            "months": months, "warnings": list(dict.fromkeys(warnings)), "estimate": True}


@router.get("/v1/forecast")
def forecast(request: Request, response: Response):
    owner = household(request, response)
    links = query_all(f"HOUSEHOLD#{owner}", "PLAID#")
    accounts = []
    incomplete = not links
    for link in links:
        item_id = link["item_id"]
        item = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"}, ConsistentRead=True).get("Item", {})
        if item.get("household_id") != owner or item.get("status") != "active" or item.get("pending_sync"):
            incomplete = True
            continue
        rows = query_all(f"PLAID#{item_id}", "ACCOUNT#")
        if not rows:
            incomplete = True
        accounts.extend(row["data"] | {"sync_completed_at": item.get("last_sync")} for row in rows)
    transactions = household_transactions(owner)
    entries = {row["sk"][9:]: row for row in query_all(f"HOUSEHOLD#{owner}", "EXPECTED#")}
    stored = query_all(f"HOUSEHOLD#{owner}", "FORECAST_TXN#")
    manual = {row["sk"][13:]: row["match"] for row in stored if "match" in row}
    suppressed = {row["sk"][13:] for row in stored if row.get("suppressed")}
    rules = query_all(f"HOUSEHOLD#{owner}", "RULE#")
    matches = list(manual.values()) + automatic_matches(owner, transactions, manual, suppressed, rules, entries)
    return calculate_projection(datetime.now(ZoneInfo("America/New_York")).date(), accounts, incomplete,
                                transactions, entries, matches)
