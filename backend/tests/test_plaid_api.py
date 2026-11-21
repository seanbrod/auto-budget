from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import plaid_api
from app.main import app


def test_direct_calls_cannot_access_any_protected_plaid_endpoint():
    client = TestClient(app)
    for method, path, body in [
        ("POST", "/v1/plaid/link-token", None),
        ("POST", "/v1/plaid/exchange", {"public_token": "test"}),
        ("GET", "/v1/plaid/items", None),
        ("DELETE", "/v1/plaid/items/item-one", None),
        ("POST", "/v1/plaid/items/item-one/sync", None),
        ("GET", "/v1/accounts", None),
    ]:
        assert client.request(method, path, json=body).status_code == 401


def test_other_household_item_is_not_accessible(monkeypatch):
    db = Mock()
    db.get_item.return_value = {"Item": {"household_id": "other", "status": "active"}}
    monkeypatch.setattr(plaid_api, "table", lambda: db)
    monkeypatch.setattr(plaid_api, "household", lambda req, resp: "mine")
    queue = Mock()
    monkeypatch.setattr(plaid_api, "enqueue", queue)
    client = TestClient(app)
    assert client.post("/v1/plaid/items/foreign/sync").status_code == 404
    assert client.delete("/v1/plaid/items/foreign").status_code == 404
    queue.assert_not_called()


def test_webhook_rejects_unsigned_body_before_queueing(monkeypatch):
    queue = Mock()
    monkeypatch.setattr(plaid_api, "enqueue", queue)
    monkeypatch.setattr(plaid_api, "plaid_client", lambda: Mock())
    response = TestClient(app).post("/v1/webhooks/plaid", json={"item_id": "one"})
    assert response.status_code == 401
    queue.assert_not_called()


def test_accounts_expose_sync_time_and_reject_incomplete_sync(monkeypatch):
    db = Mock()
    meta = {"household_id": "mine", "status": "active", "last_sync": 12345}
    db.get_item.return_value = {"Item": meta}
    monkeypatch.setattr(plaid_api, "table", lambda: db)
    monkeypatch.setattr(plaid_api, "household", lambda req, resp: "mine")
    monkeypatch.setattr(plaid_api, "query_all", lambda pk, prefix: ([{"item_id": "test"}] if prefix == "PLAID#"
                                                                   else [{"data": {"account_id": "account"}}]))
    client = TestClient(app)
    assert client.get("/v1/accounts").json()["accounts"][0]["sync_completed_at"] == 12345
    db.get_item.return_value = {"Item": meta | {"pending_sync": {"count": 1}}}
    assert client.get("/v1/accounts").status_code == 503
