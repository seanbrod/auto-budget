from unittest.mock import MagicMock, Mock

import pytest

from app import plaid_worker
from app.plaid_sync import SyncUpdate


def prepare(monkeypatch):
    db = MagicMock()
    client = Mock()
    client.call.return_value = {"accounts": [{"account_id": "account-1"}]}
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    monkeypatch.setattr(plaid_worker, "table", lambda: db)
    monkeypatch.setattr(plaid_worker, "acquire", lambda item: ("lease-1", {"cursor": "old"}))
    monkeypatch.setattr(plaid_worker, "plaid_client", lambda: client)
    monkeypatch.setattr(plaid_worker, "item_token", lambda item: "token")
    monkeypatch.setattr(plaid_worker, "clean_orphaned_journals", lambda *_: None)
    return db, client


def test_cursor_advances_only_after_writes(monkeypatch):
    db, _ = prepare(monkeypatch)
    operations = []
    db.query.side_effect = [{"Items": []}, {"Items": [
        {"sk": "stage-1", "kind": "TRANSACTION", "data": {"transaction_id": "a"}},
        {"sk": "stage-2", "kind": "TRANSACTION", "data": {"transaction_id": "b"}},
        {"sk": "stage-3", "kind": "ACCOUNT", "data": {"account_id": "account-1"}},
        {"sk": "stage-4", "kind": "REMOVE", "data": {"transaction_id": "c"}},
    ]}]
    monkeypatch.setattr(plaid_worker, "collect_update", lambda fetch, cursor: SyncUpdate(
        [{"transaction_id": "a"}], [{"transaction_id": "b"}],
        [{"transaction_id": "c"}], "new"))
    monkeypatch.setattr(plaid_worker, "checked_write", lambda item, owner, operation: operations.append(operation))
    db.update_item.side_effect = lambda **kw: operations.append(kw)
    plaid_worker.sync_item("item-1")
    assert len(operations) == 10
    assert "pending_sync" in operations[4]["UpdateExpression"]
    assert "Delete" in operations[8]
    assert operations[9]["ExpressionAttributeValues"][":cursor"] == "new"


def test_failed_write_preserves_cursor_and_releases_lease(monkeypatch):
    db, _ = prepare(monkeypatch)
    db.query.return_value = {"Items": []}
    monkeypatch.setattr(plaid_worker, "collect_update", lambda fetch, cursor: SyncUpdate(
        [{"transaction_id": "a"}], [], [], "new"))

    def fail(*_args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(plaid_worker, "checked_write", fail)
    with pytest.raises(RuntimeError):
        plaid_worker.sync_item("item-1")
    assert db.update_item.call_count == 1
    assert db.update_item.call_args.kwargs["UpdateExpression"] == "REMOVE lease_owner, lease_until"


def test_retries_published_journal_without_fetching_a_new_update(monkeypatch):
    db, client = prepare(monkeypatch)
    monkeypatch.setattr(plaid_worker, "acquire", lambda item: (
        "lease-2", {"cursor": "old", "pending_sync": {"id": "journal", "cursor": "new", "count": 1}}))
    db.query.return_value = {"Items": [{"sk": "stage-1", "kind": "TRANSACTION", "data": {"transaction_id": "a"}}]}
    writes = []
    monkeypatch.setattr(plaid_worker, "checked_write", lambda item, owner, operation: writes.append(operation))
    plaid_worker.sync_item("item-1")
    client.call.assert_not_called()
    assert len(writes) == 1
    assert db.update_item.call_args.kwargs["ExpressionAttributeValues"][":cursor"] == "new"


def test_sqs_batch_reports_only_failed_records(monkeypatch):
    db = Mock()
    db.get_item.return_value = {"Item": {"status": "active"}}
    monkeypatch.setattr(plaid_worker, "table", lambda: db)
    def sync(item):
        if item == "bad":
            raise RuntimeError("failure")

    monkeypatch.setattr(plaid_worker, "sync_item", sync)
    event = {"Records": [{"messageId": "one", "body": '{"item_id":"ok"}'},
                         {"messageId": "two", "body": '{"item_id":"bad"}'}]}
    assert plaid_worker.handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "two"}]}
