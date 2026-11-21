import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from app import rules_api, transactions_api
from app.main import app
from app.rule_matching import winning_rule
from app.spending import classification


def rule(**changes):
    return {"id": "a", "field": "name", "operator": "contains", "value": "rent",
            "direction": "outgoing", "priority": 10, "enabled": True, "created_ns": 100,
            "classification": {"kind": "excluded"}} | changes


def test_rule_matching_priority_direction_and_versions():
    txn = {"name": "  RENT   Payment ", "amount": 20, "first_seen_ns": 150}
    lower = rule(id="b", priority=20)
    old = rule(ended_ns=200)
    assert winning_rule([lower, old], txn, new_only=True) == old
    assert winning_rule([old], txn | {"first_seen_ns": 201}, new_only=True) is None
    assert winning_rule([old], txn | {"amount": -20}, new_only=True) is None
    assert winning_rule([old | {"enabled": False}], txn) is None
    assert winning_rule([old | {"operator": "exact", "value": "rent payment"}], txn) is not None
    changed = txn | {"name": "Updated name", "_rule_match_data": {"name": "Rent", "amount": 20}}
    assert winning_rule([old], changed, new_only=True) == old
    assert winning_rule([old], changed) is None
    assert winning_rule([old | {"operator": "regex", "value": r"^rent\s+pay.*"}], txn) == (
        old | {"operator": "regex", "value": r"^rent\s+pay.*"})


def test_manual_pending_override_wins():
    txn = {"transaction_id": "posted", "pending_transaction_id": "pending"}
    assert classification(txn, {"pending": {"kind": "transfer"}}) == {"kind": "transfer"}


def test_automatic_rule_respects_pending_manual_choice_and_first_seen(monkeypatch):
    rows = [{"transaction_id": "posted", "pending_transaction_id": "pending", "name": "Rent", "amount": 20,
             "first_seen_ns": 150}, {"transaction_id": "new", "name": "Rent", "amount": 20,
                                     "first_seen_ns": 150},
            {"transaction_id": "old", "name": "Rent", "amount": 20, "first_seen_ns": 0}]
    monkeypatch.setattr(transactions_api, "household_overrides", lambda owner: {"pending": {"kind": "transfer"}})
    monkeypatch.setattr(transactions_api, "query_all", lambda pk, prefix: [{"rule": rule()}])
    choices = transactions_api.effective_overrides("mine", rows)
    assert classification(rows[0], choices) == {"kind": "transfer"}
    assert choices["new"] == {"kind": "excluded"}
    assert "old" not in choices


def test_rule_preview_apply_and_household_isolation(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    with mock_aws():
        db = boto3.resource("dynamodb").create_table(
            TableName="test-table", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
        )
        monkeypatch.setattr(rules_api, "household", lambda request, response: "mine")
        monkeypatch.setattr(rules_api, "table", lambda: db)
        monkeypatch.setattr(transactions_api, "table", lambda: db)
        transaction = {"transaction_id": "one", "name": "Rent", "date": "2026-11-01", "amount": 20}
        monkeypatch.setattr(rules_api, "household_transactions", lambda owner: [transaction])
        client = TestClient(app)
        body = {"field": "name", "operator": "contains", "value": "rent", "direction": "outgoing",
                "classification": {"kind": "excluded"}}
        created = client.post("/v1/category-rules", json=body)
        assert created.status_code == 201
        assert client.post("/v1/category-rules", json=body | {"operator": "regex", "value": "(a+)+(?=x)"}).status_code == 422
        identifier = created.json()["id"]
        preview = client.post(f"/v1/category-rules/{identifier}/preview").json()
        assert preview["count"] == 1
        assert client.post(f"/v1/category-rules/{identifier}/apply", json={"preview_token": "0" * 64}).status_code == 409
        response = client.post(f"/v1/category-rules/{identifier}/apply", json={"preview_token": preview["preview_token"]})
        assert response.status_code == 200
        assert response.json()["applied"] == 1
        assert db.get_item(Key={"pk": "HOUSEHOLD#mine", "sk": "OVERRIDE#one"})["Item"]["choice"] == {"kind": "excluded"}
        assert client.post(f"/v1/category-rules/{identifier}/preview").json()["count"] == 0
        extra = [transaction | {"transaction_id": f"extra-{index:02}"} for index in range(30)]
        monkeypatch.setattr(rules_api, "household_transactions", lambda owner: extra)
        page = client.post(f"/v1/category-rules/{identifier}/preview").json()
        assert page["count"] == 30 and len(page["transactions"]) == 25 and page["truncated"]
        applied = client.post(f"/v1/category-rules/{identifier}/apply", json={"preview_token": page["preview_token"]})
        assert applied.json() == {"applied": 25, "skipped": 0, "remaining": 5}
        page = client.post(f"/v1/category-rules/{identifier}/preview").json()
        assert page["count"] == 5
        assert client.post(f"/v1/category-rules/{identifier}/apply", json={"preview_token": page["preview_token"]}).json()["applied"] == 5
        assert client.delete(f"/v1/category-rules/{identifier}").status_code == 200
        stored = db.get_item(Key={"pk": "HOUSEHOLD#mine", "sk": f"RULE#{identifier}"})["Item"]
        assert stored["deleted"] and len(stored["history"]) == 1
        monkeypatch.setattr(rules_api, "household", lambda request, response: "other")
        assert client.post(f"/v1/category-rules/{identifier}/preview").status_code == 404
