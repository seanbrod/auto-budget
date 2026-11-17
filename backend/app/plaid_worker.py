"""Household-independent sync worker; item ownership comes from stored metadata."""

import json
import logging
import os
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from app.plaid_client import PlaidClient
from app.plaid_sync import collect_update

SERIALIZER = TypeSerializer()
logger = logging.getLogger(__name__)


def serialize(value):
    return {key: SERIALIZER.serialize(val) for key, val in value.items()}


def table():
    return boto3.resource("dynamodb").Table(os.environ["HOUSEHOLD_TABLE"])


def plaid_client():
    ssm = boto3.client("ssm")
    prefix = os.environ["PLAID_PARAMETER_PREFIX"]
    credentials = json.loads(ssm.get_parameter(Name=f"{prefix}/credentials", WithDecryption=True)["Parameter"]["Value"])
    return PlaidClient(credentials["client_id"], credentials["secret"])


def item_token(item_id):
    prefix = os.environ["PLAID_PARAMETER_PREFIX"]
    return boto3.client("ssm").get_parameter(
        Name=f"{prefix}/items/{item_id}", WithDecryption=True
    )["Parameter"]["Value"]


def acquire(item_id):
    """Acquire an expiring lease; callers must run for less than 10 minutes."""
    owner = uuid.uuid4().hex
    now = int(time.time())
    try:
        result = table().update_item(
            Key={"pk": f"PLAID#{item_id}", "sk": "META"},
            UpdateExpression="SET lease_owner = :owner, lease_until = :until",
            ConditionExpression="attribute_exists(pk) AND (#status = :active) AND "
                                "(attribute_not_exists(lease_until) OR lease_until < :now)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":owner": owner, ":until": now + 600, ":now": now, ":active": "active"},
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return owner, result["Attributes"]


def checked_write(item_id, owner, operation):
    """Only the active lease holder may mutate item data, even after expiry."""
    boto3.client("dynamodb").transact_write_items(TransactItems=[
        {"ConditionCheck": {
            "TableName": os.environ["HOUSEHOLD_TABLE"],
            "Key": serialize({"pk": f"PLAID#{item_id}", "sk": "META"}),
            "ConditionExpression": "lease_owner = :owner AND lease_until > :now AND #status = :active",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": serialize({":owner": owner, ":now": int(time.time()), ":active": "active"}),
        }}, operation,
    ])


def clean_orphaned_journals(item_id, owner, pending):
    """Remove abandoned staging rows, preserving any journal awaiting replay."""
    keep_prefix = f"STAGED#{pending['id']}#" if pending else None
    args = {"KeyConditionExpression": Key("pk").eq(f"PLAID#{item_id}") & Key("sk").begins_with("STAGED#"),
            "ConsistentRead": True}
    while True:
        page = table().query(**args)
        for row in page["Items"]:
            if keep_prefix and row["sk"].startswith(keep_prefix):
                continue
            checked_write(item_id, owner, {"Delete": {"TableName": os.environ["HOUSEHOLD_TABLE"],
                          "Key": serialize({"pk": f"PLAID#{item_id}", "sk": row["sk"]})}})
        if "LastEvaluatedKey" not in page:
            return
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def sync_item(item_id):
    acquired = acquire(item_id)
    if acquired is None:
        # A duplicate queue delivery must retry, not silently lose its work.
        raise RuntimeError("Item is unavailable or another synchronization is active")
    owner, metadata = acquired
    try:
        pending = metadata.get("pending_sync")
        clean_orphaned_journals(item_id, owner, pending)
        if not pending:
            client = plaid_client()
            token = item_token(item_id)

            def fetch(cursor):
                request = {"access_token": token, "count": 500}
                if cursor:
                    request["cursor"] = cursor
                return client.call("/transactions/sync", request)

            update = collect_update(fetch, metadata.get("cursor"))
            accounts = client.call("/accounts/get", {"access_token": token})["accounts"]
            account_ids = {account["account_id"] for account in accounts}
            old_accounts = []
            account_query = {"KeyConditionExpression": Key("pk").eq(f"PLAID#{item_id}") &
                             Key("sk").begins_with("ACCOUNT#"), "ConsistentRead": True}
            while True:
                page = table().query(**account_query)
                old_accounts.extend({"account_id": row["sk"][8:]} for row in page["Items"]
                                    if row["sk"][8:] not in account_ids)
                if "LastEvaluatedKey" not in page:
                    break
                account_query["ExclusiveStartKey"] = page["LastEvaluatedKey"]
            stage_id = uuid.uuid4().hex
            number = 0
            for kind, entries in (("TRANSACTION", update.added), ("TRANSACTION", update.modified),
                                  ("ACCOUNT", accounts), ("REMOVE", update.removed),
                                  ("REMOVE_ACCOUNT", old_accounts)):
                for entry in entries:
                    record = {"pk": f"PLAID#{item_id}", "sk": f"STAGED#{stage_id}#{number:010d}",
                              "kind": kind, "data": entry}
                    checked_write(item_id, owner, {"Put": {"TableName": os.environ["HOUSEHOLD_TABLE"],
                                                           "Item": serialize(record)}})
                    number += 1
            pending = {"id": stage_id, "cursor": update.next_cursor, "count": number}
            # Publish the journal only after all entries exist. Before this point,
            # a crash cannot have changed visible account or transaction rows.
            table().update_item(
                Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                UpdateExpression="SET pending_sync = :pending",
                ConditionExpression="lease_owner = :owner AND lease_until > :now AND #status = :active",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":pending": pending, ":owner": owner,
                                           ":now": int(time.time()), ":active": "active"},
            )
        query = {"KeyConditionExpression": Key("pk").eq(f"PLAID#{item_id}") &
                 Key("sk").begins_with(f"STAGED#{pending['id']}#"), "ConsistentRead": True}
        staged = []
        while True:
            result = table().query(**query)
            staged.extend(result["Items"])
            if "LastEvaluatedKey" not in result:
                break
            query["ExclusiveStartKey"] = result["LastEvaluatedKey"]
        if len(staged) != pending["count"]:
            raise RuntimeError("Incomplete synchronization journal")
        for row in staged:
            kind, entry = row["kind"], row["data"]
            identifier = entry["account_id" if kind in ("ACCOUNT", "REMOVE_ACCOUNT") else "transaction_id"]
            record_key = {"pk": f"PLAID#{item_id}",
                          "sk": f"{'ACCOUNT' if kind in ('ACCOUNT', 'REMOVE_ACCOUNT') else 'TRANSACTION'}#{identifier}"}
            if kind in ("REMOVE", "REMOVE_ACCOUNT"):
                operation = {"Delete": {"TableName": os.environ["HOUSEHOLD_TABLE"], "Key": serialize(record_key)}}
            elif kind in ("ACCOUNT", "TRANSACTION"):
                operation = {"Put": {"TableName": os.environ["HOUSEHOLD_TABLE"],
                                     "Item": serialize(record_key | {"data": entry})}}
            else:
                raise ValueError("Invalid synchronization journal")
            checked_write(item_id, owner, operation)
        table().update_item(
            Key={"pk": f"PLAID#{item_id}", "sk": "META"},
            UpdateExpression="SET #cursor = :cursor, last_sync = :now REMOVE lease_owner, lease_until, pending_sync",
            ConditionExpression="lease_owner = :owner AND lease_until > :now AND #status = :active",
            ExpressionAttributeNames={"#cursor": "cursor", "#status": "status"},
            ExpressionAttributeValues={":cursor": pending["cursor"], ":owner": owner,
                                       ":now": int(time.time()), ":active": "active"},
        )
        # The committed cursor makes this journal unnecessary. Cleanup failures
        # cause a queue retry; they do not roll back the already committed cursor.
        with table().batch_writer() as batch:
            for row in staged:
                batch.delete_item(Key={"pk": f"PLAID#{item_id}", "sk": row["sk"]})
    except Exception:
        try:
            table().update_item(
                Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                UpdateExpression="REMOVE lease_owner, lease_until",
                ConditionExpression="lease_owner = :owner",
                ExpressionAttributeValues={":owner": owner},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        raise


def handler(event, _context):
    """SQS invokes once per record; failed records are retried by the queue."""
    if event.get("source") == "auto-budget-weekly-recovery":
        args = {"FilterExpression": Attr("sk").eq("META") & Attr("pk").begins_with("PLAID#") &
                Attr("status").eq("active"), "ProjectionExpression": "pk"}
        queue = boto3.client("sqs")
        while True:
            page = table().scan(**args)
            for item in page["Items"]:
                queue.send_message(QueueUrl=os.environ["PLAID_QUEUE_URL"],
                                   MessageBody=json.dumps({"item_id": item["pk"][6:]}))
            if "LastEvaluatedKey" not in page:
                break
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return {"status": "queued"}
    failed = []
    for record in event["Records"]:
        try:
            payload = json.loads(record["body"], parse_float=Decimal)
            item_id = payload["item_id"]
            metadata = table().get_item(Key={"pk": f"PLAID#{item_id}", "sk": "META"},
                                        ConsistentRead=True).get("Item")
            if metadata and metadata.get("status") == "active":
                sync_item(item_id)
        except Exception:  # noqa: BLE001 - preserve other records in an SQS batch
            # Never log the record body or exception: it may contain financial data.
            logger.error("Plaid synchronization failed; message will be retried")
            failed.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failed}
