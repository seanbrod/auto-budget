"""Manual, household-scoped matches between transactions and expected occurrences."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.forecast_api import key as expected_key
from app.forecast_api import versioned_occurrences
from app.plaid_api import ITEM_ID, household, query_all
from app.plaid_worker import serialize, table
from app.rule_matching import winning_rule
from app.spending import visible_transactions
from app.transactions_api import household_transactions

router = APIRouter()


class MatchInput(BaseModel):
    transaction_id: str
    entry_id: str
    occurrence_date: date


def transaction_key(owner, identifier):
    return {"pk": f"HOUSEHOLD#{owner}", "sk": f"FORECAST_TXN#{identifier}"}


def occurrence_key(owner, entry_id, day):
    return {"pk": f"HOUSEHOLD#{owner}", "sk": f"FORECAST_OCC#{entry_id}#{day}"}


def match_for_transaction(transaction, matches):
    return matches.get(transaction.get("transaction_id")) or matches.get(transaction.get("pending_transaction_id"))


def household_matches(owner):
    return {row["sk"][13:]: row["match"] for row in query_all(f"HOUSEHOLD#{owner}", "FORECAST_TXN#") if "match" in row}


def transact(actions):
    try:
        boto3.client("dynamodb").transact_write_items(TransactItems=actions)
    except ClientError as exc:
        reasons = exc.response.get("CancellationReasons", [])
        if (exc.response["Error"]["Code"] == "TransactionCanceledException" and reasons
                and all(reason.get("Code") in ("None", "ConditionalCheckFailed") for reason in reasons)):
            raise HTTPException(409, "Match or expected entry changed; refresh and try again") from None
        raise


def automatic_matches(owner, transactions, manual, suppressed, rule_rows, entries):
    """Only mutually unique transaction/occurrence candidates are matched."""
    rules = [version for row in rule_rows for version in row.get("history", [])]
    rules.extend(row["rule"] for row in rule_rows if not row.get("deleted"))
    rules = [rule for rule in rules if rule.get("forecast")]
    occupied = {(match["entry_id"], match["occurrence_date"]) for match in manual.values()}
    candidates = []
    for transaction in visible_transactions(transactions):
        identifier = transaction.get("pending_transaction_id") or transaction.get("transaction_id")
        if not isinstance(identifier, str) or not ITEM_ID.fullmatch(identifier):
            continue
        if identifier in suppressed or match_for_transaction(transaction, manual):
            continue
        rule = winning_rule(rules, transaction, new_only=True)
        if not rule:
            continue
        action = rule["forecast"]
        row = entries.get(action["entry_id"])
        if not row:
            continue
        try:
            day = date.fromisoformat(transaction["date"])
            amount = Decimal(str(transaction["amount"]))
            if (not amount.is_finite() or not amount or amount != amount.quantize(Decimal("0.01"))
                    or transaction.get("iso_currency_code") not in (None, "USD")
                    or transaction.get("unofficial_currency_code")):
                continue
            window = int(action["window_days"])
            first = day - timedelta(days=min(window, (day - date.min).days))
            last = day + timedelta(days=min(window, (date.max - day).days))
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            continue
        for occurrence, entry in versioned_occurrences(row, first, last):
            occurrence_id = (action["entry_id"], occurrence.isoformat())
            if occurrence_id in occupied or (amount < 0) != (entry["kind"] == "income"):
                continue
            tolerance = action.get("amount_tolerance_cents")
            if tolerance is not None and abs(abs(amount) * 100 - entry["amount_cents"]) > tolerance:
                continue
            candidates.append((identifier, occurrence_id, rule["id"]))
    counts_tx, counts_occ = {}, {}
    for identifier, occurrence_id, _ in candidates:
        counts_tx[identifier] = counts_tx.get(identifier, 0) + 1
        counts_occ[occurrence_id] = counts_occ.get(occurrence_id, 0) + 1
    return [{"transaction_id": identifier, "entry_id": occurrence_id[0], "occurrence_date": occurrence_id[1],
             "automatic": True, "rule_id": rule_id}
            for identifier, occurrence_id, rule_id in candidates
            if counts_tx[identifier] == 1 and counts_occ[occurrence_id] == 1]


@router.get("/v1/forecast-matches")
def list_matches(request: Request, response: Response):
    owner = household(request, response)
    stored = query_all(f"HOUSEHOLD#{owner}", "FORECAST_TXN#")
    manual = {row["sk"][13:]: row["match"] for row in stored if "match" in row}
    suppressed = {row["sk"][13:] for row in stored if row.get("suppressed")}
    rules = query_all(f"HOUSEHOLD#{owner}", "RULE#")
    entries = {row["sk"][9:]: row for row in query_all(f"HOUSEHOLD#{owner}", "EXPECTED#")}
    auto = automatic_matches(owner, household_transactions(owner), manual, suppressed, rules, entries)
    return {"matches": list(manual.values()) + auto}


@router.post("/v1/forecast-matches", status_code=201)
def create_match(body: MatchInput, request: Request, response: Response):
    owner = household(request, response)
    if not ITEM_ID.fullmatch(body.transaction_id):
        raise HTTPException(404, "Transaction not found")
    rows = visible_transactions(household_transactions(owner))
    matches = [row for row in rows if row.get("transaction_id") == body.transaction_id]
    if len(matches) != 1:
        raise HTTPException(404, "Transaction not found")
    transaction = matches[0]
    entry_key = expected_key(owner, body.entry_id)
    row = table().get_item(Key=entry_key, ConsistentRead=True).get("Item")
    if not row:
        raise HTTPException(404, "Expected entry not found")
    found = versioned_occurrences(row, body.occurrence_date, body.occurrence_date)
    if len(found) != 1:
        raise HTTPException(422, "Expected occurrence not found")
    try:
        amount = Decimal(str(transaction["amount"]))
        if (not amount.is_finite() or not amount or transaction.get("iso_currency_code") not in (None, "USD")
                or transaction.get("unofficial_currency_code")
                or (amount < 0) != (found[0][1]["kind"] == "income")):
            raise ValueError
    except (KeyError, InvalidOperation, ValueError):
        raise HTTPException(422, "Transaction direction or currency does not match") from None
    identifier = transaction.get("pending_transaction_id") or body.transaction_id
    if not isinstance(identifier, str) or not ITEM_ID.fullmatch(identifier):
        raise HTTPException(422, "Invalid transaction linkage")
    match = {"transaction_id": identifier, "entry_id": body.entry_id,
             "occurrence_date": body.occurrence_date.isoformat()}
    revision = int(row.get("revision", 0))
    condition = "revision = :revision" if revision else "attribute_not_exists(revision)"
    check = {"TableName": table().name, "Key": serialize(entry_key),
             "ConditionExpression": "attribute_exists(pk) AND " + condition,
             "UpdateExpression": "SET revision = :next",
             "ExpressionAttributeValues": serialize({":next": revision + 1} | ({":revision": revision} if revision else {}))}
    actions = [{"Update": check}]
    for record_key in (transaction_key(owner, identifier), occurrence_key(owner, body.entry_id, match["occurrence_date"])):
        actions.append({"Put": {"TableName": table().name, "Item": serialize(record_key | {"match": match}),
                                "ConditionExpression": "attribute_not_exists(pk)"}})
    actions[1]["Put"]["ConditionExpression"] = "attribute_not_exists(pk) OR suppressed = :suppressed"
    actions[1]["Put"]["ExpressionAttributeValues"] = serialize({":suppressed": True})
    transact(actions)
    return match


@router.delete("/v1/forecast-matches/{transaction_id}", status_code=204)
def delete_match(transaction_id: str, request: Request, response: Response):
    owner = household(request, response)
    if not ITEM_ID.fullmatch(transaction_id):
        raise HTTPException(404, "Match not found")
    row = table().get_item(Key=transaction_key(owner, transaction_id), ConsistentRead=True).get("Item")
    if not row:
        stored = query_all(f"HOUSEHOLD#{owner}", "FORECAST_TXN#")
        manual = {item["sk"][13:]: item["match"] for item in stored if "match" in item}
        suppressed = {item["sk"][13:] for item in stored if item.get("suppressed")}
        rules = query_all(f"HOUSEHOLD#{owner}", "RULE#")
        entries = {item["sk"][9:]: item for item in query_all(f"HOUSEHOLD#{owner}", "EXPECTED#")}
        auto = automatic_matches(owner, household_transactions(owner), manual, suppressed, rules, entries)
        if not any(match["transaction_id"] == transaction_id for match in auto):
            raise HTTPException(404, "Match not found")
        try:
            table().put_item(Item=transaction_key(owner, transaction_id) | {"suppressed": True},
                             ConditionExpression="attribute_not_exists(pk)")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(409, "Match changed; refresh and try again") from None
            raise
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    if "match" not in row:
        raise HTTPException(404, "Match not found")
    match = row["match"]
    actions = []
    for record_key in (transaction_key(owner, transaction_id),
                       occurrence_key(owner, match["entry_id"], match["occurrence_date"])):
        actions.append({"Delete": {"TableName": table().name, "Key": serialize(record_key),
                                    "ConditionExpression": "#match = :match", "ExpressionAttributeNames": {"#match": "match"},
                                    "ExpressionAttributeValues": serialize({":match": match})}})
    actions[0] = {"Put": {"TableName": table().name,
                          "Item": serialize(transaction_key(owner, transaction_id) | {"suppressed": True}),
                          "ConditionExpression": "#match = :match", "ExpressionAttributeNames": {"#match": "match"},
                          "ExpressionAttributeValues": serialize({":match": match})}}
    transact(actions)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
