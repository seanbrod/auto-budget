"""Household-scoped transaction review and manual classifications."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, model_validator

from app.budget_api import IDENTIFIER, budget_key, category_key
from app.plaid_api import ITEM_ID, household, query_all
from app.plaid_worker import table
from app.spending import calculate_month, classification, visible_transactions

router = APIRouter()


class Classification(BaseModel):
    kind: Literal["spending", "refund", "transfer", "card_payment", "income", "excluded"]
    category_id: str | None = None

    @model_validator(mode="after")
    def validate_category(self):
        if self.kind in ("spending", "refund"):
            if not self.category_id or not IDENTIFIER.fullmatch(self.category_id):
                raise ValueError("A category is required")
        elif self.category_id is not None:
            raise ValueError("Category applies only to spending and refunds")
        return self


def household_transactions(owner):
    transactions = []
    for link in query_all(f"HOUSEHOLD#{owner}", "PLAID#"):
        item_id = link["item_id"]
        meta = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                                ConsistentRead=True).get("Item", {})
        if meta.get("household_id") != owner or meta.get("status") != "active":
            continue
        if meta.get("pending_sync"):
            raise HTTPException(503, "Account synchronization in progress; try again shortly")
        for row in query_all(f"PLAID#{item_id}", "TRANSACTION#"):
            transactions.append(row["data"] | {"item_id": item_id})
    return transactions


def household_overrides(owner):
    return {row["sk"][9:]: row["choice"] for row in query_all(f"HOUSEHOLD#{owner}", "OVERRIDE#")}


@router.get("/v1/transactions")
def transactions(request: Request, response: Response, month: str | None = None):
    owner = household(request, response)
    if month is not None:
        budget_key(owner, month)
    overrides = household_overrides(owner)
    rows = visible_transactions(household_transactions(owner))
    if month is not None:
        rows = [row for row in rows if str(row.get("date", "")).startswith(month + "-")]
    rows.sort(key=lambda row: (str(row.get("date", "")), str(row.get("transaction_id", ""))), reverse=True)
    return {"transactions": [row | {"classification": classification(row, overrides)} for row in rows]}


@router.put("/v1/transactions/{transaction_id}/classification")
def set_classification(transaction_id: str, body: Classification, request: Request, response: Response):
    owner = household(request, response)
    if not ITEM_ID.fullmatch(transaction_id):
        raise HTTPException(404, "Transaction not found")
    matches = [t for t in household_transactions(owner) if t.get("transaction_id") == transaction_id]
    if len(matches) != 1:
        raise HTTPException(404, "Transaction not found")
    if body.category_id and not table().get_item(Key=category_key(owner, body.category_id),
                                                 ConsistentRead=True).get("Item"):
        raise HTTPException(422, "Unknown category")
    choice = body.model_dump(exclude_none=True)
    table().put_item(Item={"pk": f"HOUSEHOLD#{owner}", "sk": f"OVERRIDE#{transaction_id}", "choice": choice})
    return {"classification": choice}


@router.get("/v1/dashboard")
def dashboard(request: Request, response: Response, month: str):
    owner = household(request, response)
    key = budget_key(owner, month)
    budget = table().get_item(Key=key, ConsistentRead=True).get("Item", {})
    totals, unclassified = calculate_month(household_transactions(owner), household_overrides(owner), month)
    allowances = {k: int(v) for k, v in budget.get("allowances", {}).items()}
    ids = set(allowances) | set(totals)
    return {"month": month, "categories": {key: {"planned_cents": allowances.get(key, 0),
                                                   "spent_cents": totals.get(key, 0),
                                                   "remaining_cents": allowances.get(key, 0) - totals.get(key, 0)}
                                           for key in ids}, "unclassified_count": unclassified}
