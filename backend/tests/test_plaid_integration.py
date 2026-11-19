"""Exercise the Sandbox flow with mocked AWS services and a fake Plaid API."""

import hashlib
import json
import time

import boto3
import jwt
import pytest
from botocore.exceptions import EndpointConnectionError
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from moto import mock_aws

from app import plaid_api, plaid_worker
from app.main import app
from app.plaid_client import PlaidError


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    monkeypatch.setenv("PLAID_PARAMETER_PREFIX", "/test/plaid/sandbox")
    monkeypatch.setenv("PLAID_WEBHOOK_URL", "https://example.com/v1/webhooks/plaid")
    with mock_aws():
        db = boto3.resource("dynamodb").create_table(
            TableName="test-table", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
        )
        queue = boto3.client("sqs").create_queue(QueueName="test-queue")["QueueUrl"]
        monkeypatch.setenv("PLAID_QUEUE_URL", queue)
        monkeypatch.setattr(plaid_api, "household", lambda request, response: "family-one")

        class FakePlaid:
            def call(self, path, data):
                if path == "/item/public_token/exchange":
                    return {"item_id": "item-one", "access_token": "sandbox-access-token"}
                if path == "/transactions/sync":
                    return {"added": [{"transaction_id": "txn-one", "amount": 12}], "modified": [],
                            "removed": [], "next_cursor": "cursor-one", "has_more": False}
                if path == "/accounts/get":
                    return {"accounts": [{"account_id": "account-one", "name": "Checking", "balances": {"current": 42}}]}
                if path == "/item/remove":
                    return {}
                raise AssertionError(path)

        plaid = FakePlaid()
        monkeypatch.setattr(plaid_api, "plaid_client", lambda: plaid)
        monkeypatch.setattr(plaid_worker, "plaid_client", lambda: plaid)
        yield db, queue


def test_exchange_queue_sync_accounts_and_disconnect(sandbox):
    db, queue = sandbox
    client = TestClient(app)
    response = client.post("/v1/plaid/exchange", json={"public_token": "public-test"})
    assert response.status_code == 200
    assert "sandbox-access-token" not in response.text
    assert client.get("/v1/plaid/items").json() == {"items": [{"item_id": "item-one", "status": "active"}]}
    message = boto3.client("sqs").receive_message(QueueUrl=queue)["Messages"][0]
    assert plaid_worker.handler({"Records": [{"messageId": "msg", "body": message["Body"]}]}, None) == {
        "batchItemFailures": []}
    assert client.get("/v1/accounts").json()["accounts"][0]["name"] == "Checking"
    assert db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})["Item"]["cursor"] == "cursor-one"
    assert db.get_item(Key={"pk": "PLAID#item-one", "sk": "TRANSACTION#txn-one"})["Item"]["data"]["amount"] == 12
    assert client.delete("/v1/plaid/items/item-one").json() == {"status": "disconnected"}
    assert client.get("/v1/plaid/items").json() == {"items": []}
    assert "Item" not in db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})


def test_failed_application_replays_published_journal(sandbox, monkeypatch):
    db, queue = sandbox
    assert TestClient(app).post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    db.put_item(Item={"pk": "PLAID#item-one", "sk": "STAGED#abandoned#0000000000", "data": {"amount": 5}})
    message = boto3.client("sqs").receive_message(QueueUrl=queue)["Messages"][0]
    event = {"Records": [{"messageId": "msg", "body": message["Body"]}]}
    original = plaid_worker.checked_write
    failed = False

    def fail_once(item_id, owner, operations):
        nonlocal failed
        if any(operation.get("Put", {}).get("Item", {}).get("sk", {}).get("S", "").startswith("TRANSACTION#")
               for operation in operations) and not failed:
            failed = True
            raise RuntimeError("simulated interruption")
        original(item_id, owner, operations)

    monkeypatch.setattr(plaid_worker, "checked_write", fail_once)
    assert plaid_worker.handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "msg"}]}
    item = db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})["Item"]
    assert "cursor" not in item and item["pending_sync"]["cursor"] == "cursor-one"
    assert plaid_worker.handler(event, None) == {"batchItemFailures": []}
    item = db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})["Item"]
    assert item["cursor"] == "cursor-one" and "pending_sync" not in item
    assert "Item" not in db.get_item(Key={"pk": "PLAID#item-one", "sk": "STAGED#abandoned#0000000000"})
    assert json.loads(message["Body"]) == {"item_id": "item-one"}


def test_interrupted_staging_discards_orphans_before_retry(sandbox, monkeypatch):
    db, queue = sandbox
    assert TestClient(app).post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    original_write = plaid_worker.checked_write
    calls = 0
    def interrupt_second_batch(item_id, owner, operations):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        original_write(item_id, owner, operations)

    from app.plaid_sync import SyncUpdate
    monkeypatch.setattr(plaid_worker, "collect_update", lambda fetch, cursor: SyncUpdate(
        [{"transaction_id": f"txn-{i}"} for i in range(10)], [], [], "new-cursor"))
    monkeypatch.setattr(plaid_worker, "checked_write", interrupt_second_batch)
    message = boto3.client("sqs").receive_message(QueueUrl=queue)["Messages"][0]
    event = {"Records": [{"messageId": "msg", "body": message["Body"]}]}
    assert plaid_worker.handler(event, None)["batchItemFailures"]
    item = db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})["Item"]
    assert "cursor" not in item and "pending_sync" not in item
    monkeypatch.setattr(plaid_worker, "checked_write", original_write)
    assert plaid_worker.handler(event, None) == {"batchItemFailures": []}
    item = db.get_item(Key={"pk": "PLAID#item-one", "sk": "META"})["Item"]
    assert item["cursor"] == "new-cursor"
    rows = db.query(KeyConditionExpression=plaid_worker.Key("pk").eq("PLAID#item-one"))["Items"]
    assert sum(row["sk"].startswith("TRANSACTION#") for row in rows) == 10
    assert not any(row["sk"].startswith("STAGED#") for row in rows)


def test_signed_webhook_queues_and_weekly_recovery_finds_items(sandbox, monkeypatch, caplog):
    _, queue = sandbox
    client = TestClient(app)
    assert client.post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    sqs = boto3.client("sqs")
    initial = sqs.receive_message(QueueUrl=queue)["Messages"][0]
    sqs.delete_message(QueueUrl=queue, ReceiptHandle=initial["ReceiptHandle"])
    private = ec.generate_private_key(ec.SECP256R1())
    key = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    key.update({"kid": "key-one", "alg": "ES256", "use": "sig", "expired_at": None})

    class WebhookClient:
        def call(self, path, data):
            assert path == "/webhook_verification_key/get" and data == {"key_id": "key-one"}
            return {"key": key}

    monkeypatch.setattr(plaid_api, "plaid_client", lambda: WebhookClient())
    body = b'{"webhook_type":"TRANSACTIONS","webhook_code":"SYNC_UPDATES_AVAILABLE","item_id":"item-one"}'
    token = jwt.encode({"iat": int(time.time()), "request_body_sha256": hashlib.sha256(body).hexdigest()},
                       private, algorithm="ES256", headers={"kid": "key-one"})
    assert client.post("/v1/webhooks/plaid", content=body, headers={"Plaid-Verification": token}).status_code == 200
    assert client.post("/v1/webhooks/plaid", content=body + b" ", headers={"Plaid-Verification": token}).status_code == 401
    message = sqs.receive_message(QueueUrl=queue)["Messages"][0]
    assert json.loads(message["Body"]) == {"item_id": "item-one", "source": "webhook"}
    assert f"verified webhook queued item_ref={plaid_worker.item_ref('item-one')}" in caplog.text
    assert "item-one" not in caplog.text
    sqs.delete_message(QueueUrl=queue, ReceiptHandle=message["ReceiptHandle"])
    assert plaid_worker.handler({"source": "auto-budget-weekly-recovery"}, None) == {"status": "queued"}
    assert json.loads(sqs.receive_message(QueueUrl=queue)["Messages"][0]["Body"]) == {
        "item_id": "item-one", "source": "recovery"}
    assert f"recovery queued item_ref={plaid_worker.item_ref('item-one')}" in caplog.text


def test_queue_outage_preserves_connection_and_reports_recovery(sandbox, monkeypatch):
    def fail_queue(_item_id):
        raise EndpointConnectionError(endpoint_url="https://example.invalid")

    monkeypatch.setattr(plaid_api, "enqueue", fail_queue)
    client = TestClient(app)
    response = client.post("/v1/plaid/exchange", json={"public_token": "test"})
    assert response.status_code == 200 and response.json()["status"] == "recovery_pending"
    assert client.get("/v1/plaid/items").json()["items"] == [{"item_id": "item-one", "status": "active"}]


def test_disconnect_timeout_remains_visible_and_item_not_found_finishes_cleanup(sandbox, monkeypatch):
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    class FailedRemoval:
        def call(self, path, data):
            raise PlaidError("PLAID_UNAVAILABLE")

    monkeypatch.setattr(plaid_api, "plaid_client", lambda: FailedRemoval())
    assert client.delete("/v1/plaid/items/item-one").status_code == 500
    assert client.get("/v1/plaid/items").json()["items"] == [{"item_id": "item-one", "status": "deleting"}]
    assert client.get("/v1/accounts").json() == {"accounts": []}
    class Removed:
        def call(self, path, data):
            raise PlaidError("ITEM_NOT_FOUND")

    monkeypatch.setattr(plaid_api, "plaid_client", lambda: Removed())
    assert client.delete("/v1/plaid/items/item-one").status_code == 200
    assert client.get("/v1/plaid/items").json() == {"items": []}


def test_exchange_recovers_when_transaction_committed_but_response_lost(sandbox, monkeypatch):
    original = boto3.client
    dynamodb = original("dynamodb")
    transact = dynamodb.transact_write_items
    def lost_response(**kwargs):
        transact(**kwargs)
        raise EndpointConnectionError(endpoint_url="https://example.invalid")

    monkeypatch.setattr(dynamodb, "transact_write_items", lost_response)
    monkeypatch.setattr(boto3, "client", lambda service, *args, **kwargs:
                        dynamodb if service == "dynamodb" else original(service, *args, **kwargs))
    client = TestClient(app)
    assert client.post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    assert client.get("/v1/plaid/items").json()["items"] == [{"item_id": "item-one", "status": "active"}]


def test_exchange_recovers_when_secret_write_committed_but_response_lost(sandbox, monkeypatch):
    original = boto3.client
    ssm = original("ssm")
    put = ssm.put_parameter
    def lost_response(**kwargs):
        put(**kwargs)
        raise EndpointConnectionError(endpoint_url="https://example.invalid")

    monkeypatch.setattr(ssm, "put_parameter", lost_response)
    monkeypatch.setattr(boto3, "client", lambda service, *args, **kwargs:
                        ssm if service == "ssm" else original(service, *args, **kwargs))
    client = TestClient(app)
    assert client.post("/v1/plaid/exchange", json={"public_token": "test"}).status_code == 200
    assert client.get("/v1/plaid/items").json()["items"] == [{"item_id": "item-one", "status": "active"}]
