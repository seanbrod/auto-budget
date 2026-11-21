"""Household matching rules and explicit historical application."""

import hashlib
import json
import time
import uuid
from typing import Literal

import boto3
import re2
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.budget_api import IDENTIFIER, category_key
from app.plaid_api import household, query_all
from app.plaid_worker import serialize, table
from app.rule_matching import compile_pattern, normalized, winning_rule
from app.spending import classification, visible_transactions
from app.transactions_api import (
    Classification,
    household_overrides,
    household_transactions,
)

router = APIRouter()
APPLY_BATCH_SIZE = 25


class ForecastAction(BaseModel):
    entry_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    window_days: int = Field(ge=0, le=31, strict=True)
    amount_tolerance_cents: int | None = Field(default=None, ge=0, le=100_000_000, strict=True)


class RuleInput(BaseModel):
    field: Literal["merchant_name", "name"]
    operator: Literal["exact", "contains", "regex"]
    value: str = Field(min_length=1, max_length=100)
    direction: Literal["outgoing", "incoming", "either"]
    account_id: str | None = Field(default=None, max_length=128)
    priority: int = Field(default=100, ge=0, le=10000, strict=True)
    enabled: bool = True
    classification: Classification | None = None
    forecast: ForecastAction | None = None

    @model_validator(mode="after")
    def normalize_value(self):
        if not self.classification and not self.forecast:
            raise ValueError("At least one action is required")
        self.value = self.value.strip() if self.operator == "regex" else normalized(self.value)
        if not self.value:
            raise ValueError("Match value is required")
        if self.operator == "regex":
            try:
                compile_pattern(self.value)
            except re2.error:
                raise ValueError("Invalid or unsupported regular expression") from None
        return self


class ApplyInput(BaseModel):
    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")


def rules(owner):
    return [row["rule"] for row in query_all(f"HOUSEHOLD#{owner}", "RULE#") if not row.get("deleted")]


def key(owner, rule_id):
    return {"pk": f"HOUSEHOLD#{owner}", "sk": f"RULE#{rule_id}"}


def existing_rule(owner, rule_id):
    if not IDENTIFIER.fullmatch(rule_id):
        raise HTTPException(404, "Rule not found")
    row = table().get_item(Key=key(owner, rule_id), ConsistentRead=True).get("Item")
    if not row or row.get("deleted"):
        raise HTTPException(404, "Rule not found")
    return row["rule"]


def validate_category(owner, body):
    category_id = body.classification.category_id if body.classification else None
    if category_id and not table().get_item(Key=category_key(owner, category_id), ConsistentRead=True).get("Item"):
        raise HTTPException(422, "Unknown category")
    if body.forecast:
        from app.forecast_api import key as expected_key

        entry = table().get_item(Key=expected_key(owner, body.forecast.entry_id), ConsistentRead=True).get("Item")
        if not entry or entry.get("retired_on"):
            raise HTTPException(422, "Unknown or retired expected entry")


def replace_rule(owner, rule_id, row, new_row):
    try:
        table().put_item(Item=key(owner, rule_id) | new_row,
                         ConditionExpression="#rule.created_ns = :old AND attribute_not_exists(deleted)",
                         ExpressionAttributeNames={"#rule": "rule"},
                         ExpressionAttributeValues={":old": row["rule"]["created_ns"]})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "Rule changed; reload and try again") from None
        raise


@router.get("/v1/category-rules")
def list_rules(request: Request, response: Response):
    owner = household(request, response)
    return {"rules": sorted(rules(owner), key=lambda r: (r["priority"], r["id"]))}


@router.post("/v1/category-rules", status_code=201)
def create_rule(body: RuleInput, request: Request, response: Response):
    owner = household(request, response)
    validate_category(owner, body)
    rule_id = uuid.uuid4().hex
    rule = body.model_dump(exclude_none=True) | {"id": rule_id, "created_ns": time.time_ns()}
    table().put_item(Item=key(owner, rule_id) | {"rule": rule}, ConditionExpression="attribute_not_exists(pk)")
    return rule


@router.put("/v1/category-rules/{rule_id}")
def update_rule(rule_id: str, body: RuleInput, request: Request, response: Response):
    owner = household(request, response)
    existing_rule(owner, rule_id)
    row = table().get_item(Key=key(owner, rule_id), ConsistentRead=True)["Item"]
    validate_category(owner, body)
    # Edits apply prospectively; historical applications remain unchanged.
    now = time.time_ns()
    rule = body.model_dump(exclude_none=True) | {"id": rule_id, "created_ns": now}
    history = row.get("history", []) + [row["rule"] | {"ended_ns": now}]
    replace_rule(owner, rule_id, row, {"rule": rule, "history": history})
    return rule


@router.delete("/v1/category-rules/{rule_id}")
def delete_rule(rule_id: str, request: Request, response: Response):
    owner = household(request, response)
    existing_rule(owner, rule_id)
    row = table().get_item(Key=key(owner, rule_id), ConsistentRead=True)["Item"]
    history = row.get("history", []) + [row["rule"] | {"ended_ns": time.time_ns()}]
    replace_rule(owner, rule_id, row, {"rule": row["rule"], "history": history, "deleted": True})
    return {"status": "deleted"}


def candidates(owner, rule_id):
    selected = existing_rule(owner, rule_id)
    if not selected.get("classification"):
        raise HTTPException(422, "Rule has no classification action")
    all_rules = [rule for rule in rules(owner) if rule.get("classification")]
    overrides = household_overrides(owner)
    result = []
    for transaction in visible_transactions(household_transactions(owner)):
        if classification(transaction, overrides):
            continue
        winner = winning_rule(all_rules, transaction)
        if winner and winner["id"] == selected["id"]:
            result.append(transaction)
    return sorted(result, key=lambda transaction: transaction["transaction_id"])


def preview_token(rule, found):
    # Include the complete displayed transaction contents so a changed preview
    # cannot be applied based on stale information.
    content = json.dumps({"rule": rule, "transactions": found}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


@router.post("/v1/category-rules/{rule_id}/preview")
def preview(rule_id: str, request: Request, response: Response):
    owner = household(request, response)
    rule = existing_rule(owner, rule_id)
    found = candidates(owner, rule_id)
    return {"count": len(found), "transactions": found[:APPLY_BATCH_SIZE],
            "truncated": len(found) > APPLY_BATCH_SIZE,
            "preview_token": preview_token(rule, found)}


@router.post("/v1/category-rules/{rule_id}/apply")
def apply(rule_id: str, body: ApplyInput, request: Request, response: Response):
    owner = household(request, response)
    rule = existing_rule(owner, rule_id)
    found = candidates(owner, rule_id)
    if preview_token(rule, found) != body.preview_token:
        raise HTTPException(409, "Preview changed; review it again")
    client = boto3.client("dynamodb")
    applied = 0
    batch = found[:APPLY_BATCH_SIZE]
    for transaction in batch:
        identifier = transaction["transaction_id"]
        record_key = {"pk": f"HOUSEHOLD#{owner}", "sk": f"OVERRIDE#{identifier}"}
        pending_id = transaction.get("pending_transaction_id")
        actions = [{"ConditionCheck": {"TableName": table().name, "Key": serialize(key(owner, rule_id)),
                    "ConditionExpression": "#rule.created_ns = :created AND attribute_not_exists(deleted)",
                    "ExpressionAttributeNames": {"#rule": "rule"},
                    "ExpressionAttributeValues": serialize({":created": rule["created_ns"]})}},
                   {"Put": {"TableName": table().name, "Item": serialize(record_key | {
            "choice": rule["classification"], "rule_id": rule_id}),
            "ConditionExpression": "attribute_not_exists(pk)"}}]
        if pending_id and pending_id != identifier:
            actions.append({"ConditionCheck": {"TableName": table().name, "Key": serialize({
                "pk": f"HOUSEHOLD#{owner}", "sk": f"OVERRIDE#{pending_id}"}),
                "ConditionExpression": "attribute_not_exists(pk)"}})
        try:
            client.transact_write_items(TransactItems=actions)
            applied += 1
        except ClientError as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if (exc.response["Error"]["Code"] != "TransactionCanceledException" or not reasons
                    or any(reason.get("Code") not in ("None", "ConditionalCheckFailed") for reason in reasons)):
                raise
            # A manual edit may have arrived after the preview; never overwrite it.
    return {"applied": applied, "skipped": len(batch) - applied, "remaining": len(found) - len(batch)}
