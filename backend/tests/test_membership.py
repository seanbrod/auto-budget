from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Response

from app.main import handler, me


def request_for(claims):
    request = Mock()
    request.scope = {"aws.event": {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}}
    return request


def test_member_can_access_household(monkeypatch):
    client = Mock()
    client.get_item.return_value = {"Item": {"household_id": {"S": "household-1"}}}
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    monkeypatch.setattr("app.main.boto3.client", lambda _: client)
    response = Response()
    assert me(request_for({"sub": "user-1", "token_use": "access"}), response) == {
        "household_id": "household-1"
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get_item.call_args.kwargs["Key"]["pk"] == {"S": "USER#user-1"}


def test_nonmember_is_rejected(monkeypatch):
    client = Mock()
    client.get_item.return_value = {}
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    monkeypatch.setattr("app.main.boto3.client", lambda _: client)
    with pytest.raises(HTTPException) as error:
        me(request_for({"sub": "user-1", "token_use": "access"}), Response())
    assert error.value.status_code == 403


def test_id_token_is_rejected():
    with pytest.raises(HTTPException) as error:
        me(request_for({"sub": "user-1", "token_use": "id"}), Response())
    assert error.value.status_code == 401


def test_gateway_event_passes_verified_claims_to_application(monkeypatch):
    client = Mock()
    client.get_item.return_value = {"Item": {"household_id": {"S": "household-1"}}}
    monkeypatch.setenv("HOUSEHOLD_TABLE", "test-table")
    monkeypatch.setattr("app.main.boto3.client", lambda _: client)
    event = {
        "version": "2.0", "routeKey": "GET /api/me", "rawPath": "/api/me",
        "rawQueryString": "", "headers": {"host": "example.com"},
        "requestContext": {
            "http": {"method": "GET", "path": "/api/me", "sourceIp": "127.0.0.1"},
            "authorizer": {"jwt": {"claims": {"sub": "user-1", "token_use": "access"}}},
        },
        "isBase64Encoded": False,
    }
    result = handler(event, {})
    assert result["statusCode"] == 200
    assert result["headers"]["cache-control"] == "no-store"
    assert '"household_id":"household-1"' in result["body"]
