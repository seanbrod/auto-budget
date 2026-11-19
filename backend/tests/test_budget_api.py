from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import budget_api
from app.main import app


def test_budget_routes_require_authentication():
    client = TestClient(app)
    for method, path, body in [
        ("GET", "/v1/categories", None),
        ("POST", "/v1/categories", {"name": "Food"}),
        ("PUT", "/v1/categories/" + "a" * 32, {"name": "Food"}),
        ("GET", "/v1/budgets/2026-11", None),
        ("PUT", "/v1/budgets/2026-11", {"allowances": {}}),
    ]:
        assert client.request(method, path, json=body).status_code == 401


def test_budget_uses_household_keys_and_rejects_foreign_category(monkeypatch):
    db = Mock()
    db.get_item.return_value = {}
    monkeypatch.setattr(budget_api, "household", lambda request, response: "mine")
    monkeypatch.setattr(budget_api, "table", lambda: db)
    client = TestClient(app)
    category_id = "a" * 32
    assert client.put("/v1/budgets/2026-11", json={"allowances": {category_id: 100}}).status_code == 422
    db.get_item.assert_called_with(Key={"pk": "HOUSEHOLD#mine", "sk": f"CATEGORY#{category_id}"},
                                   ConsistentRead=True)
    db.put_item.assert_not_called()
    assert client.put(f"/v1/categories/{category_id}", json={"name": "Food"}).status_code == 404


def test_budget_validates_month_and_integer_cents(monkeypatch):
    db = Mock()
    db.get_item.return_value = {"Item": {"name": "Food"}}
    monkeypatch.setattr(budget_api, "household", lambda request, response: "mine")
    monkeypatch.setattr(budget_api, "table", lambda: db)
    client = TestClient(app)
    category_id = "a" * 32
    assert client.put("/v1/budgets/2026-13", json={"allowances": {}}).status_code == 422
    assert client.put("/v1/budgets/2026-11", json={"allowances": {category_id: 1.5}}).status_code == 422
    assert client.put("/v1/budgets/2026-11", json={"allowances": {category_id: -1}}).status_code == 422
    assert client.put("/v1/budgets/2026-11", json={"allowances": {category_id: 1234}}).status_code == 200
    assert db.put_item.call_args.kwargs["Item"]["pk"] == "HOUSEHOLD#mine"
    assert db.put_item.call_args.kwargs["Item"]["allowances"][category_id] == 1234
