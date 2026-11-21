from datetime import date

from fastapi.testclient import TestClient

from app.forecast_projection import calculate_projection
from app.main import app


def test_projection_uses_balances_excludes_fulfilled_and_spans_year():
    accounts = [{"type": "depository", "subtype": "checking", "balances": {"available": 1000, "current": 1100}, "sync_completed_at": 1},
                {"type": "credit", "subtype": "credit card", "balances": {"current": 200}, "sync_completed_at": 1}]
    entry = {"entry": {"kind": "expense", "amount_cents": 10000, "start_date": "2026-11-25", "recurrence": "monthly"}}
    transaction = {"transaction_id": "posted", "pending_transaction_id": "pending", "pending": False}
    match = {"transaction_id": "pending", "entry_id": "rent", "occurrence_date": "2026-11-25"}
    result = calculate_projection(date(2026, 11, 21), accounts, False, [transaction], {"rent": entry}, [match])
    assert result["starting_cents"] == 80000
    assert result["months"][0]["projected_cents"] == 80000
    assert result["months"][0]["fulfilled_count"] == 1
    assert result["months"][1]["projected_cents"] == 70000
    assert result["months"][-1]["month"] == "2027-10"


def test_missing_balance_withholds_totals_and_pending_warns():
    entry = {"entry": {"kind": "income", "amount_cents": 10000, "start_date": "2026-11-25", "recurrence": "once"}}
    match = {"transaction_id": "pending", "entry_id": "salary", "occurrence_date": "2026-11-25"}
    account = {"type": "depository", "subtype": "savings", "balances": {}, "sync_completed_at": 1}
    result = calculate_projection(date(2026, 11, 21), [account], False,
                                  [{"transaction_id": "pending", "pending": True}], {"salary": entry}, [match])
    assert result["starting_cents"] is None
    assert all(month["projected_cents"] is None for month in result["months"])
    assert result["months"][0]["income_cents"] == 0
    assert any("Pending" in warning for warning in result["warnings"])
    assert calculate_projection(date(2026, 11, 21), [], False, [], {}, [])["starting_cents"] is None


def test_projection_requires_authentication():
    response = TestClient(app).get('/v1/forecast')
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


def test_cash_fallback_and_missing_matched_transaction_are_visible():
    account = {"type": "depository", "subtype": "checking", "balances": {"available": None, "current": 200}, "sync_completed_at": 100}
    entry = {"entry": {"kind": "expense", "amount_cents": 1000, "start_date": "2026-11-25", "recurrence": "once"}}
    match = {"transaction_id": "gone", "entry_id": "rent", "occurrence_date": "2026-11-25"}
    result = calculate_projection(date(2026, 11, 21), [account], False, [], {"rent": entry}, [match])
    assert result["starting_cents"] == 20000
    assert result["months"][0]["projected_cents"] == 19000
    assert any("current balance" in warning for warning in result["warnings"])
    assert any("unavailable" in warning for warning in result["warnings"])
    assert calculate_projection(date(2026, 11, 21), [account], True, [], {}, [])["starting_cents"] is None


def test_matched_pending_occurrence_is_labeled():
    account = {"type": "credit", "subtype": "credit card", "balances": {"current": 20}, "sync_completed_at": 1}
    entry = {"entry": {"kind": "expense", "amount_cents": 1000, "start_date": "2026-11-25", "recurrence": "once"}}
    match = {"transaction_id": "pending", "entry_id": "bill", "occurrence_date": "2026-11-25"}
    result = calculate_projection(date(2026, 11, 21), [account], False,
                                  [{"transaction_id": "pending", "pending": True}], {"bill": entry}, [match])
    assert result["months"][0]["pending_fulfilled_count"] == 1
    assert result["months"][0]["projected_cents"] == -2000
    assert any("no balance adjustment" in warning for warning in result["warnings"])
