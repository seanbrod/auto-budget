"""Sandbox Plaid routes. Access is scoped to the authenticated household."""

import json
import logging
import os
import re
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.plaid_client import PlaidError
from app.plaid_webhook import InvalidWebhook, verify_webhook
from app.plaid_worker import item_ref, item_token, plaid_client, serialize, table

router = APIRouter()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ExchangeRequest(BaseModel):
    public_token: str = Field(min_length=1, max_length=512)


def household(request, response):
    # Imported lazily to share exactly the authorization used by /api/me.
    from app.main import me

    return me(request, response)["household_id"]


def owned_item(household_id, item_id):
    if not ITEM_ID.fullmatch(item_id):
        raise HTTPException(404, "Item not found")
    item = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"}, ConsistentRead=True).get("Item")
    if not item or item.get("household_id") != household_id or item.get("status") != "active":
        raise HTTPException(404, "Item not found")
    return item


def query_all(pk, prefix):
    condition = Key("pk").eq(pk)
    if prefix:
        condition = condition & Key("sk").begins_with(prefix)
    args = {"KeyConditionExpression": condition, "ConsistentRead": True}
    result = []
    while True:
        page = table().query(**args)
        result.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            return result
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def enqueue(item_id, *, source=None):
    payload = {"item_id": item_id}
    if source:
        payload["source"] = source
    boto3.client("sqs").send_message(QueueUrl=os.environ["PLAID_QUEUE_URL"],
                                     MessageBody=json.dumps(payload))


def delete_item_rows(item_id, owner, deadline):
    """Remove item data in bounded, lease-checked batches; resume on retry."""
    pk = f"PLAID#{item_id}"
    args = {"KeyConditionExpression": Key("pk").eq(pk), "ConsistentRead": True, "Limit": 25}
    while True:
        page = table().query(**args)
        rows = [row for row in page["Items"] if row["sk"] != "META"]
        for start in range(0, len(rows), 8):
            if time.monotonic() >= deadline:
                return False
            actions = [{"ConditionCheck": {
                "TableName": os.environ["HOUSEHOLD_TABLE"],
                "Key": serialize({"pk": pk, "sk": "META"}),
                "ConditionExpression": "lease_owner = :owner AND lease_until > :now AND #status = :deleting",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": serialize({":owner": owner, ":now": int(time.time()),
                                                         ":deleting": "deleting"}),
            }}]
            actions.extend({"Delete": {"TableName": os.environ["HOUSEHOLD_TABLE"],
                                       "Key": serialize({"pk": pk, "sk": row["sk"]})}}
                           for row in rows[start:start + 8])
            boto3.client("dynamodb").transact_write_items(TransactItems=actions)
        if "LastEvaluatedKey" not in page:
            return True
        if time.monotonic() >= deadline:
            return False
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


@router.post("/v1/plaid/link-token")
def link_token(request: Request, response: Response):
    household_id = household(request, response)
    result = plaid_client().call("/link/token/create", {
        "user": {"client_user_id": household_id}, "client_name": "Auto Budget",
        "products": ["transactions"], "country_codes": ["US"], "language": "en",
        "webhook": os.environ["PLAID_WEBHOOK_URL"],
    })
    return {"link_token": result["link_token"]}


@router.post("/v1/plaid/exchange")
def exchange(body: ExchangeRequest, request: Request, response: Response):
    household_id = household(request, response)
    client = plaid_client()
    result = client.call("/item/public_token/exchange", {"public_token": body.public_token})
    item_id, token = result["item_id"], result["access_token"]
    if not ITEM_ID.fullmatch(item_id):
        raise HTTPException(502, "Invalid Plaid response")
    # The secret is written outside Terraform and never returned to the browser.
    parameter = f"{os.environ['PLAID_PARAMETER_PREFIX']}/items/{item_id}"
    ssm = boto3.client("ssm")
    try:
        ssm.put_parameter(Name=parameter, Value=token, Type="SecureString", Tier="Standard", Overwrite=False)
    except (BotoCoreError, ClientError, OSError):
        # A timeout may mean the write succeeded. Do not revoke a token until
        # we know whether the intended value was stored.
        try:
            saved = ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
        except (BotoCoreError, ClientError, OSError):
            logger.error("Plaid credential storage needs investigation item_ref=%s", item_ref(item_id))
            raise HTTPException(503, "Connection status is uncertain; investigate before reconnecting") from None
        if saved != token:
            logger.error("Plaid credential storage conflict item_ref=%s", item_ref(item_id))
            raise HTTPException(503, "Connection status is uncertain; investigate before reconnecting") from None
    # An ambiguous transaction failure must not revoke a potentially active Item.
    transaction = [
            {"Put": {"TableName": os.environ["HOUSEHOLD_TABLE"], "Item": serialize({
                "pk": f"PLAID#{item_id}", "sk": "META", "household_id": household_id, "status": "active"}),
                "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Put": {"TableName": os.environ["HOUSEHOLD_TABLE"], "Item": serialize({
                "pk": f"HOUSEHOLD#{household_id}", "sk": f"PLAID#{item_id}", "item_id": item_id}),
                "ConditionExpression": "attribute_not_exists(pk)"}},
    ]
    try:
        boto3.client("dynamodb").transact_write_items(TransactItems=transaction)
    except (BotoCoreError, ClientError, OSError):
        # A lost response may hide a committed transaction. Strong reads of
        # both records distinguish that case from an incomplete setup.
        try:
            meta = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                                    ConsistentRead=True).get("Item", {})
            link = table().get_item(Key={"pk": f"HOUSEHOLD#{household_id}", "sk": f"PLAID#{item_id}"},
                                    ConsistentRead=True).get("Item", {})
        except (BotoCoreError, ClientError, OSError):
            meta, link = {}, {}
        if meta.get("household_id") != household_id or link.get("item_id") != item_id:
            logger.error("Plaid connection setup needs investigation item_ref=%s", item_ref(item_id))
            raise HTTPException(503, "Connection status is uncertain; investigate before reconnecting") from None
    try:
        enqueue(item_id)
    except (BotoCoreError, ClientError, OSError):
        # The Item is already saved; tell the user to retry sync rather than
        # re-linking and accidentally creating another Item.
        logger.error("Initial Plaid synchronization could not be queued")
        return {"item_id": item_id, "status": "recovery_pending"}
    return {"item_id": item_id, "status": "sync_queued"}


@router.get("/v1/plaid/items")
def items(request: Request, response: Response):
    household_id = household(request, response)
    result = []
    for entry in query_all(f"HOUSEHOLD#{household_id}", "PLAID#"):
        meta = table().get_item(Key={"pk": f"PLAID#{entry['item_id']}", "sk": "META"},
                                ConsistentRead=True).get("Item", {})
        if meta.get("household_id") == household_id:
            result.append({"item_id": entry["item_id"], "status": meta.get("status", "unknown")})
    return {"items": result}


@router.post("/v1/plaid/items/{item_id}/sync", status_code=202)
def sync(item_id: str, request: Request, response: Response):
    owned_item(household(request, response), item_id)
    enqueue(item_id)
    return {"status": "queued"}


@router.delete("/v1/plaid/items/{item_id}")
def delete_item(item_id: str, request: Request, response: Response):
    deadline = time.monotonic() + 18
    household_id = household(request, response)
    if not ITEM_ID.fullmatch(item_id):
        raise HTTPException(404, "Item not found")
    key = {"pk": f"PLAID#{item_id}", "sk": "META"}
    existing = table().get_item(Key=key, ConsistentRead=True).get("Item")
    if not existing or existing.get("household_id") != household_id:
        raise HTTPException(404, "Item not found")
    owner, now = uuid.uuid4().hex, int(time.time())
    try:
        item = table().update_item(
            Key=key, UpdateExpression="SET #status = :deleting, lease_owner = :owner, lease_until = :until",
            ConditionExpression="household_id = :household AND (#status = :active OR #status = :deleting) "
                                "AND (attribute_not_exists(lease_until) OR lease_until < :now)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":household": household_id, ":owner": owner, ":now": now,
                                       ":until": now + 60, ":active": "active", ":deleting": "deleting"},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "Item is busy; try again later") from None
        raise
    try:
        if not item.get("remote_removed"):
            # A rejected token does not prove the subscription ended. Keep the
            # local connection for recovery instead of claiming success.
            try:
                plaid_client().call("/item/remove", {"access_token": item_token(item_id)})
            except PlaidError as exc:
                # Plaid documents ITEM_NOT_FOUND as already removed or revoked.
                if exc.code != "ITEM_NOT_FOUND":
                    raise
            table().update_item(Key=key, UpdateExpression="SET remote_removed = :yes",
                                ConditionExpression="lease_owner = :owner AND lease_until > :now",
                                ExpressionAttributeValues={":yes": True, ":owner": owner, ":now": int(time.time())})
        if not delete_item_rows(item_id, owner, deadline) or time.monotonic() >= deadline:
            table().update_item(Key=key, UpdateExpression="REMOVE lease_owner, lease_until",
                                ConditionExpression="lease_owner = :owner",
                                ExpressionAttributeValues={":owner": owner})
            return {"status": "cleanup_pending"}
        try:
            boto3.client("ssm").delete_parameter(Name=f"{os.environ['PLAID_PARAMETER_PREFIX']}/items/{item_id}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ParameterNotFound":
                raise
        boto3.client("dynamodb").transact_write_items(TransactItems=[
            {"Delete": {"TableName": os.environ["HOUSEHOLD_TABLE"], "Key": serialize(key),
                        "ConditionExpression": "lease_owner = :owner AND lease_until > :now",
                        "ExpressionAttributeValues": serialize({":owner": owner, ":now": int(time.time())})}},
            {"Delete": {"TableName": os.environ["HOUSEHOLD_TABLE"], "Key": serialize({
                "pk": f"HOUSEHOLD#{household_id}", "sk": f"PLAID#{item_id}"})}},
        ])
    except Exception:
        try:
            table().update_item(Key=key, UpdateExpression="REMOVE lease_owner, lease_until",
                                ConditionExpression="lease_owner = :owner", ExpressionAttributeValues={":owner": owner})
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        raise
    return {"status": "disconnected"}


@router.get("/v1/accounts")
def accounts(request: Request, response: Response):
    household_id = household(request, response)
    accounts = []
    for entry in query_all(f"HOUSEHOLD#{household_id}", "PLAID#"):
        item_id = entry["item_id"]
        item = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                                ConsistentRead=True).get("Item", {})
        if item.get("household_id") == household_id and item.get("status") == "active":
            accounts.extend(row["data"] for row in query_all(f"PLAID#{item_id}", "ACCOUNT#"))
    return {"accounts": accounts}


@router.post("/v1/webhooks/plaid")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("plaid-verification", "")
    client = plaid_client()
    try:
        verify_webhook(signature, body,
                       lambda key_id: client.call("/webhook_verification_key/get", {"key_id": key_id})["key"])
    except InvalidWebhook:
        raise HTTPException(401, "Invalid webhook") from None
    try:
        payload = json.loads(body)
        item_id = payload["item_id"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid webhook") from None
    if (payload.get("webhook_type") == "TRANSACTIONS" and payload.get("webhook_code") == "SYNC_UPDATES_AVAILABLE"
            and isinstance(item_id, str) and ITEM_ID.fullmatch(item_id)):
        # Queue failures must return an error so Plaid retries delivery.
        enqueue(item_id, source="webhook")
        logger.info("Plaid verified webhook queued item_ref=%s", item_ref(item_id))
    return {"received": True}
