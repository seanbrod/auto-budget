from datetime import date
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import forecast_api
from app.main import app


def test_recurrence_boundaries():
    entry = {"start_date": "2024-01-31", "end_date": None, "recurrence": "monthly"}
    assert forecast_api.occurrences(entry, date(2024, 2, 1), date(2024, 4, 30)) == [
        date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)]
    entry = {"start_date": "2024-02-29", "end_date": "2028-02-29", "recurrence": "annual"}
    assert forecast_api.occurrences(entry, date(2025, 1, 1), date(2028, 12, 31)) == [
        date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)]
    entry = {"start_date": "2026-12-25", "end_date": "2027-01-08", "recurrence": "biweekly"}
    assert forecast_api.occurrences(entry, date(2027, 1, 1), date(2027, 2, 1)) == [date(2027, 1, 8)]
    entry["recurrence"] = "once"
    assert forecast_api.occurrences(entry, date(2027, 1, 1), date(2027, 2, 1)) == []


def test_expected_auth_validation_and_household_isolation(monkeypatch):
    client = TestClient(app)
    body = {"name": "Rent", "kind": "expense", "amount_cents": 10000,
            "start_date": "2026-11-30", "end_date": None, "recurrence": "monthly"}
    assert client.get('/v1/expected').status_code == 401
    assert client.post('/v1/expected', json=body).status_code == 401
    db = Mock()
    db.get_item.return_value = {}
    monkeypatch.setattr(forecast_api, 'household', lambda request, response: 'mine')
    monkeypatch.setattr(forecast_api, 'table', lambda: db)
    assert client.put('/v1/expected/' + 'a' * 32, json={**body, 'effective_date': '2026-12-01'}).status_code == 404
    db.get_item.assert_called_with(Key={'pk': 'HOUSEHOLD#mine', 'sk': 'EXPECTED#' + 'a' * 32}, ConsistentRead=True)
    assert client.delete('/v1/expected/' + 'a' * 32 + '?effective_date=2026-12-01').status_code == 404
    assert client.post('/v1/expected', json={**body, 'amount_cents': 1.5}).status_code == 422
    assert client.post('/v1/expected', json={**body, 'end_date': '2026-01-01'}).status_code == 422
    assert client.post('/v1/expected', json=body).status_code == 201
    assert db.put_item.call_args.kwargs['Item']['pk'] == 'HOUSEHOLD#mine'


def test_versioned_occurrences_preserve_prior_amounts():
    old = {"name": "Rent", "amount_cents": 150000, "start_date": "2026-01-31",
           "end_date": None, "recurrence": "monthly"}
    new = {**old, "amount_cents": 160000}
    row = {"entry": new, "effective_date": "2026-03-01",
           "history": [{"effective_date": "2026-01-31", "entry": old}]}
    found = forecast_api.versioned_occurrences(row, date(2026, 2, 1), date(2026, 4, 30))
    assert [(day.isoformat(), entry["amount_cents"]) for day, entry in found] == [
        ("2026-02-28", 150000), ("2026-03-31", 160000), ("2026-04-30", 160000)]


def test_update_requires_later_effective_date_and_saves_history(monkeypatch):
    db = Mock()
    original = {"name": "Rent", "kind": "expense", "amount_cents": 150000,
                "start_date": "2026-01-31", "end_date": None, "recurrence": "monthly"}
    db.get_item.return_value = {"Item": {"entry": original}}
    monkeypatch.setattr(forecast_api, "household", lambda request, response: "mine")
    monkeypatch.setattr(forecast_api, "table", lambda: db)
    client = TestClient(app)
    path = "/v1/expected/" + "a" * 32
    assert client.put(path, json={**original, "effective_date": "2026-01-31"}).status_code == 422
    response = client.put(path, json={**original, "amount_cents": 160000, "effective_date": "2026-03-01"})
    assert response.status_code == 200
    assert response.json()["history"][0]["entry"]["amount_cents"] == 150000
    assert db.update_item.call_args.kwargs["ExpressionAttributeValues"][":next"] == 1


def test_retirement_keeps_history_and_stops_occurrences(monkeypatch):
    entry = {"name": "Rent", "kind": "expense", "amount_cents": 160000,
             "start_date": "2026-01-31", "end_date": None, "recurrence": "monthly"}
    old = {**entry, "amount_cents": 150000}
    row = {"entry": entry, "effective_date": "2026-03-01", "revision": 1,
           "history": [{"effective_date": "2026-01-31", "entry": old}]}
    db = Mock()
    db.get_item.return_value = {"Item": row}
    monkeypatch.setattr(forecast_api, "household", lambda request, response: "mine")
    monkeypatch.setattr(forecast_api, "table", lambda: db)
    client = TestClient(app)
    path = "/v1/expected/" + "a" * 32
    assert client.delete(path + "?effective_date=2026-02-01").status_code == 422
    assert client.delete(path + "?effective_date=2026-03-31").status_code == 204
    db.delete_item.assert_not_called()
    assert db.update_item.call_args.kwargs["ExpressionAttributeValues"][":revision"] == 1
    retired = {**row, "retired_on": "2026-03-31"}
    found = forecast_api.versioned_occurrences(retired, date(2026, 1, 1), date(2026, 5, 1))
    assert [(day.isoformat(), version["amount_cents"]) for day, version in found] == [
        ("2026-01-31", 150000), ("2026-02-28", 150000)]
    db.get_item.return_value = {"Item": retired}
    assert client.delete(path + "?effective_date=2026-03-31").status_code == 204
    assert client.put(path, json={**entry, "effective_date": "2026-04-01"}).status_code == 409
    assert client.delete(path + "?effective_date=2026-04-01").status_code == 409
