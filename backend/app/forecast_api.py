"""Household expected cash flows and calendar recurrence expansion."""

import calendar
import uuid
from datetime import date, timedelta
from typing import Literal

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from app.budget_api import IDENTIFIER
from app.plaid_api import household, query_all
from app.plaid_worker import table

router = APIRouter()


class ExpectedInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["income", "expense"]
    amount_cents: StrictInt = Field(ge=1, le=100_000_000)
    start_date: date
    end_date: date | None = None
    recurrence: Literal["once", "weekly", "biweekly", "monthly", "annual"]

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date and (self.recurrence == "once" or self.end_date < self.start_date):
            raise ValueError("Invalid end date")
        return self


class ExpectedUpdate(ExpectedInput):
    effective_date: date


def occurrences(entry, first, last):
    """Inclusive bounds; monthly and annual dates clamp to the month's last day."""
    start = date.fromisoformat(entry["start_date"])
    end = date.fromisoformat(entry["end_date"]) if entry.get("end_date") else date.max
    last = min(last, end)
    if last < start or last < first:
        return []
    recurrence = entry["recurrence"]
    if recurrence == "once":
        return [start] if first <= start <= last else []
    if recurrence in ("weekly", "biweekly"):
        step = 7 if recurrence == "weekly" else 14
        offset = max(0, (first - start).days + step - 1) // step
        if offset * step > (date.max - start).days:
            return []
        current = start + timedelta(days=offset * step)
        result = []
        while current <= last:
            result.append(current)
            if (last - current).days < step:
                break
            current += timedelta(days=step)
        return result
    months = 1 if recurrence == "monthly" else 12
    offset = max(0, ((first.year - start.year) * 12 + first.month - start.month) // months)
    result = []
    while True:
        index = start.year * 12 + start.month - 1 + offset * months
        year, month = divmod(index, 12)
        if year > 9999:
            break
        month += 1
        current = date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
        if current > last:
            break
        if current >= first:
            result.append(current)
        offset += 1
    return result


def key(owner, entry_id):
    if not IDENTIFIER.fullmatch(entry_id):
        raise HTTPException(404, "Expected entry not found")
    return {"pk": f"HOUSEHOLD#{owner}", "sk": f"EXPECTED#{entry_id}"}


def public(row):
    return {"id": row["sk"][9:], **row["entry"],
            "effective_date": row.get("effective_date", row["entry"]["start_date"]),
            "history": row.get("history", []), "retired_on": row.get("retired_on")}


def reject_matched_occurrences(owner, entry_id, effective):
    rows = query_all(f"HOUSEHOLD#{owner}", f"FORECAST_OCC#{entry_id}#")
    if any(row["match"]["occurrence_date"] >= effective for row in rows):
        raise HTTPException(409, "Unmatch affected occurrences before changing this entry")


def versioned_occurrences(row, first, last):
    """Expand each definition only within its inclusive effective interval."""
    if row.get("retired_on"):
        retired = date.fromisoformat(row["retired_on"])
        if first >= retired:
            return []
        last = min(last, retired - timedelta(days=1))
    versions = row.get("history", []) + [{"effective_date": row.get("effective_date",
                                                        row["entry"]["start_date"]), "entry": row["entry"]}]
    result = []
    for index, version in enumerate(versions):
        begin = max(first, date.fromisoformat(version["effective_date"]))
        finish = last
        if index + 1 < len(versions):
            finish = min(finish, date.fromisoformat(versions[index + 1]["effective_date"]) - timedelta(days=1))
        result.extend((day, version["entry"]) for day in occurrences(version["entry"], begin, finish))
    return result


@router.get("/v1/expected")
def list_expected(request: Request, response: Response):
    owner = household(request, response)
    return {"entries": [public(row) for row in query_all(f"HOUSEHOLD#{owner}", "EXPECTED#")]}


@router.post("/v1/expected", status_code=201)
def create_expected(body: ExpectedInput, request: Request, response: Response):
    owner = household(request, response)
    row = {**key(owner, uuid.uuid4().hex), "entry": body.model_dump(mode="json")}
    table().put_item(Item=row, ConditionExpression="attribute_not_exists(pk)")
    return public(row)


@router.put("/v1/expected/{entry_id}")
def update_expected(entry_id: str, body: ExpectedUpdate, request: Request, response: Response):
    owner = household(request, response)
    item_key = key(owner, entry_id)
    row = table().get_item(Key=item_key, ConsistentRead=True).get("Item")
    if not row:
        raise HTTPException(404, "Expected entry not found")
    if row.get("retired_on"):
        raise HTTPException(409, "Retired entries cannot be edited")
    effective = body.effective_date.isoformat()
    previous = row.get("effective_date", row["entry"]["start_date"])
    if effective <= previous:
        raise HTTPException(422, "Effective date must be after the previous version")
    reject_matched_occurrences(owner, entry_id, effective)
    history = row.get("history", [])
    if len(history) >= 200:
        raise HTTPException(422, "Too many versions")
    history = history + [{"effective_date": previous, "entry": row["entry"]}]
    revision = int(row.get("revision", 0))
    condition = "revision = :revision" if revision else "attribute_not_exists(revision)"
    values = {":entry": body.model_dump(mode="json", exclude={"effective_date"}),
              ":effective": effective, ":history": history, ":next": revision + 1}
    if revision:
        values[":revision"] = revision
    try:
        table().update_item(Key=item_key,
                            UpdateExpression="SET #entry = :entry, effective_date = :effective, "
                                             "history = :history, revision = :next",
                            ConditionExpression=condition + " AND attribute_exists(pk) AND attribute_not_exists(retired_on)",
                            ExpressionAttributeNames={"#entry": "entry"}, ExpressionAttributeValues=values)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "Entry changed; reload and try again") from None
        raise
    return {"id": entry_id, **values[":entry"], "effective_date": effective, "history": history}


@router.delete("/v1/expected/{entry_id}", status_code=204)
def retire_expected(entry_id: str, effective_date: date, request: Request, response: Response):
    """Stop occurrences on and after the effective date, retaining all definitions."""
    owner = household(request, response)
    item_key = key(owner, entry_id)
    row = table().get_item(Key=item_key, ConsistentRead=True).get("Item")
    if not row:
        raise HTTPException(404, "Expected entry not found")
    retired = effective_date.isoformat()
    if row.get("retired_on"):
        if row["retired_on"] == retired:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        raise HTTPException(409, "Entry is already retired")
    if retired < row.get("effective_date", row["entry"]["start_date"]):
        raise HTTPException(422, "Retirement cannot precede the latest version")
    reject_matched_occurrences(owner, entry_id, retired)
    revision = int(row.get("revision", 0))
    condition = "revision = :revision" if revision else "attribute_not_exists(revision)"
    values = {":retired": retired, ":next": revision + 1}
    if revision:
        values[":revision"] = revision
    try:
        table().update_item(Key=item_key, UpdateExpression="SET retired_on = :retired, revision = :next",
                            ConditionExpression=condition + " AND attribute_exists(pk) AND attribute_not_exists(retired_on)",
                            ExpressionAttributeValues=values)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "Entry changed; reload and try again") from None
        raise
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
