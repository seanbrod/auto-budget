import io
import json
from urllib.error import HTTPError

import pytest

from app.plaid_client import PlaidClient, PlaidError
from app.plaid_sync import PaginationMutation


def test_client_restricts_requests_to_sandbox_and_allowed_endpoints(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return b'{"accounts":[]}'

    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 10
        return Response()

    monkeypatch.setattr("app.plaid_client.urlopen", open_request)
    client = PlaidClient("client-id", "secret")
    assert client.call("/accounts/get", {"access_token": "access-token"}) == {"accounts": []}
    assert requests[0].full_url == "https://sandbox.plaid.com/accounts/get"
    assert json.loads(requests[0].data) == {"access_token": "access-token"}
    with pytest.raises(ValueError):
        client.call("/transactions/refresh", {})


def test_translates_pagination_mutation_without_exposing_response(monkeypatch):
    def fail(_request, timeout):
        body = json.dumps({"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"}).encode()
        raise HTTPError("https://sandbox.plaid.com", 400, "error", {}, io.BytesIO(body))

    monkeypatch.setattr("app.plaid_client.urlopen", fail)
    with pytest.raises(PaginationMutation):
        PlaidClient("client", "secret").call("/transactions/sync", {})


def test_plaid_error_does_not_expose_body(monkeypatch):
    def fail(_request, timeout):
        raise HTTPError("https://sandbox.plaid.com", 400, "error", {},
                        io.BytesIO(b'{"error_code":"INVALID_INPUT","error_message":"sensitive"}'))

    monkeypatch.setattr("app.plaid_client.urlopen", fail)
    with pytest.raises(PlaidError) as result:
        PlaidClient("client", "secret").call("/accounts/get", {})
    assert str(result.value) == "Plaid request failed"
