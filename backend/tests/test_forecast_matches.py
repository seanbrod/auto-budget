import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from app import forecast_api, forecast_matches
from app.main import app


def test_manual_match_is_unique_reversible_and_inherits_pending(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("HOUSEHOLD_TABLE", "matches")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    client = TestClient(app)
    assert client.get("/v1/forecast-matches").status_code == 401
    with mock_aws():
        db = boto3.resource("dynamodb").create_table(
            TableName="matches", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}])
        monkeypatch.setattr(forecast_matches, "household", lambda request, response: "mine")
        monkeypatch.setattr(forecast_matches, "table", lambda: db)
        monkeypatch.setattr(forecast_api, "table", lambda: db)
        monkeypatch.setattr(forecast_api, "household", lambda request, response: "mine")
        monkeypatch.setattr(forecast_api, "query_all", lambda pk, prefix: [
            row for row in db.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(pk)
                                    & boto3.dynamodb.conditions.Key("sk").begins_with(prefix))["Items"]])
        entry_id = "a" * 32
        entry = {"name": "Rent", "kind": "expense", "amount_cents": 50000,
                 "start_date": "2026-01-01", "end_date": None, "recurrence": "monthly"}
        db.put_item(Item={**forecast_api.key("mine", entry_id), "entry": entry})
        transaction = {"transaction_id": "posted", "pending_transaction_id": "pending", "amount": 500}
        monkeypatch.setattr(forecast_matches, "household_transactions", lambda owner: [transaction, {"transaction_id": "other", "amount": 500}])
        body = {"transaction_id": "posted", "entry_id": entry_id, "occurrence_date": "2026-02-01"}
        assert client.post("/v1/forecast-matches", json=body).status_code == 201
        assert client.post("/v1/forecast-matches", json=body | {"transaction_id": "other"}).status_code == 409
        assert client.post("/v1/forecast-matches", json=body | {"occurrence_date": "2026-03-01"}).status_code == 409
        stored = db.get_item(Key=forecast_matches.transaction_key("mine", "pending"))["Item"]["match"]
        assert forecast_matches.match_for_transaction(transaction, {"pending": stored}) == stored
        assert client.put(f"/v1/expected/{entry_id}", json=entry | {"effective_date": "2026-02-01"}).status_code == 409
        assert client.delete("/v1/forecast-matches/pending").status_code == 204
        assert client.post("/v1/forecast-matches", json=body | {"transaction_id": "other"}).status_code == 201
        monkeypatch.setattr(forecast_matches, "household", lambda request, response: "other-household")
        assert client.delete("/v1/forecast-matches/other").status_code == 404
        assert client.post("/v1/forecast-matches", json=body).status_code == 404


def test_automatic_matches_require_mutual_uniqueness_and_respect_overrides():
    entry_id = "a" * 32
    entry = {"entry": {"kind": "expense", "amount_cents": 50000, "start_date": "2026-01-01",
                       "end_date": None, "recurrence": "monthly"}}
    rule = {"id": "b", "field": "name", "operator": "exact", "value": "rent", "direction": "outgoing",
            "priority": 10, "enabled": True, "created_ns": 100,
            "forecast": {"entry_id": entry_id, "window_days": 5, "amount_tolerance_cents": 1000}}
    txn = {"transaction_id": "one", "name": "Rent", "amount": 505, "date": "2026-02-02", "first_seen_ns": 101}
    run = lambda rows, manual={}, suppressed=set(), rules=[{"rule": rule}]: forecast_matches.automatic_matches(
        "mine", rows, manual, suppressed, rules, {entry_id: entry})
    assert run([txn])[0]["occurrence_date"] == "2026-02-01"
    assert run([txn | {"first_seen_ns": 100}]) == []
    assert run([txn | {"amount": 520}]) == []
    assert run([txn | {"amount": -500}]) == []
    assert run([txn], suppressed={"one"}) == []
    assert run([txn], manual={"other": {"entry_id": entry_id, "occurrence_date": "2026-02-01"}}) == []
    assert run([txn, txn | {"transaction_id": "two"}]) == []
    weekly = {"entry": entry["entry"] | {"recurrence": "weekly"}}
    assert forecast_matches.automatic_matches("mine", [txn], {}, set(), [{"rule": rule}], {entry_id: weekly}) == []
    assert run([txn], rules=[{"rule": rule | {"enabled": False}}]) == []


def test_unmatching_automatic_candidate_persists_suppression(monkeypatch):
    for key, value in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test",
                       "AWS_SECRET_ACCESS_KEY": "test", "HOUSEHOLD_TABLE": "matches"}.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        db = boto3.resource("dynamodb").create_table(
            TableName="matches", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}])
        monkeypatch.setattr(forecast_matches, "table", lambda: db)
        monkeypatch.setattr(forecast_matches, "household", lambda request, response: "mine")
        monkeypatch.setattr(forecast_matches, "household_transactions", lambda owner: [
            {"transaction_id": "one", "name": "Rent", "amount": 500, "date": "2026-02-01", "first_seen_ns": 2}])
        entry_id = "a" * 32
        entry = {"kind": "expense", "amount_cents": 50000, "start_date": "2026-01-01", "recurrence": "monthly"}
        db.put_item(Item={"pk": "HOUSEHOLD#mine", "sk": "EXPECTED#" + entry_id, "entry": entry})
        rule = {"id": "b", "field": "name", "operator": "exact", "value": "rent", "direction": "outgoing",
                "priority": 10, "enabled": True, "created_ns": 1,
                "forecast": {"entry_id": entry_id, "window_days": 5}}
        db.put_item(Item={"pk": "HOUSEHOLD#mine", "sk": "RULE#b", "rule": rule})
        client = TestClient(app)
        assert len(client.get("/v1/forecast-matches").json()["matches"]) == 1
        assert client.delete("/v1/forecast-matches/one").status_code == 204
        assert client.get("/v1/forecast-matches").json()["matches"] == []
