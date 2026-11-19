from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import transactions_api
from app.main import app


def test_transaction_routes_require_auth_and_are_not_cacheable():
    client = TestClient(app)
    for method, path, body in [
        ("GET", "/v1/transactions", None),
        ("GET", "/v1/dashboard?month=2026-11", None),
        ("PUT", "/v1/transactions/foreign/classification", {"kind": "excluded"}),
    ]:
        response = client.request(method, path, json=body)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


def test_cannot_classify_another_households_transaction(monkeypatch):
    monkeypatch.setattr(transactions_api, "household", lambda request, response: "mine")
    monkeypatch.setattr(transactions_api, "household_transactions", lambda owner: [])
    db = Mock()
    monkeypatch.setattr(transactions_api, "table", lambda: db)
    response = TestClient(app).put("/v1/transactions/foreign/classification", json={"kind": "excluded"})
    assert response.status_code == 404
    db.put_item.assert_not_called()
