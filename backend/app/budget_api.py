"""Household-owned category and calendar-month allowance records."""

import re
import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictInt, field_validator

from app.plaid_api import household, query_all
from app.plaid_worker import table

router = APIRouter()
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])\Z")


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value


class BudgetInput(BaseModel):
    # Integer cents avoid rounding and binary floating-point errors.
    allowances: dict[str, StrictInt] = Field(max_length=200)

    @field_validator("allowances")
    @classmethod
    def validate_allowances(cls, values):
        if any(not IDENTIFIER.fullmatch(k) or isinstance(v, bool) or v < 0 or v > 100_000_000
               for k, v in values.items()):
            raise ValueError("Invalid allowance")
        return values


def category_key(household_id, category_id):
    return {"pk": f"HOUSEHOLD#{household_id}", "sk": f"CATEGORY#{category_id}"}


@router.get("/v1/categories")
def categories(request: Request, response: Response):
    owner = household(request, response)
    return {"categories": [{"id": row["sk"][9:], "name": row["name"]}
                           for row in query_all(f"HOUSEHOLD#{owner}", "CATEGORY#")]}


@router.post("/v1/categories", status_code=201)
def create_category(body: CategoryInput, request: Request, response: Response):
    owner = household(request, response)
    category_id = uuid.uuid4().hex
    table().put_item(Item={**category_key(owner, category_id), "name": body.name},
                     ConditionExpression="attribute_not_exists(pk)")
    return {"id": category_id, "name": body.name}


@router.put("/v1/categories/{category_id}")
def update_category(category_id: str, body: CategoryInput, request: Request, response: Response):
    owner = household(request, response)
    if not IDENTIFIER.fullmatch(category_id) or not table().get_item(
        Key=category_key(owner, category_id), ConsistentRead=True
    ).get("Item"):
        raise HTTPException(404, "Category not found")
    table().update_item(Key=category_key(owner, category_id), UpdateExpression="SET #name = :name",
                        ConditionExpression="attribute_exists(pk)", ExpressionAttributeNames={"#name": "name"},
                        ExpressionAttributeValues={":name": body.name})
    return {"id": category_id, "name": body.name}


def budget_key(owner, month):
    if not MONTH.fullmatch(month) or month.startswith("0000-"):
        raise HTTPException(422, "Invalid month")
    return {"pk": f"HOUSEHOLD#{owner}", "sk": f"BUDGET#{month}"}


@router.get("/v1/budgets/{month}")
def get_budget(month: str, request: Request, response: Response):
    key = budget_key(household(request, response), month)
    item = table().get_item(Key=key, ConsistentRead=True).get("Item", {})
    return {"month": month, "allowances": {k: int(v) for k, v in item.get("allowances", {}).items()}}


@router.put("/v1/budgets/{month}")
def put_budget(month: str, body: BudgetInput, request: Request, response: Response):
    owner = household(request, response)
    key = budget_key(owner, month)
    for category_id in body.allowances:
        if not table().get_item(Key=category_key(owner, category_id), ConsistentRead=True).get("Item"):
            raise HTTPException(422, "Unknown category")
    table().put_item(Item={**key, "allowances": {k: Decimal(v) for k, v in body.allowances.items()}})
    return {"month": month, "allowances": body.allowances}
