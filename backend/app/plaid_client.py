"""Small, Sandbox-only Plaid API client.

The fixed origin prevents a deployment configuration mistake from sending
Sandbox credentials to an arbitrary host or the Production environment.
"""

import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.plaid_sync import PaginationMutation


class PlaidError(Exception):
    """A Plaid request failed; messages intentionally omit response bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("Plaid request failed")


class PlaidClient:
    def __init__(self, client_id: str, secret: str):
        if not client_id or not secret:
            raise ValueError("Plaid credentials are not configured")
        self.client_id = client_id
        self.secret = secret

    def call(self, path: str, data: dict) -> dict:
        allowed = {
            "/link/token/create", "/item/public_token/exchange", "/item/remove",
            "/accounts/get", "/transactions/sync", "/webhook_verification_key/get",
        }
        if path not in allowed:
            raise ValueError("Plaid endpoint is not enabled")
        payload = json.dumps(data, separators=(",", ":")).encode()
        request = Request(
            "https://sandbox.plaid.com" + path,
            data=payload,
            headers={"Content-Type": "application/json", "Plaid-Client-Id": self.client_id,
                     "Plaid-Secret": self.secret},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read(), parse_float=Decimal)
        except HTTPError as exc:
            try:
                code = json.loads(exc.read()).get("error_code", "PLAID_ERROR")
            except (ValueError, AttributeError):
                code = "PLAID_ERROR"
            if code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                raise PaginationMutation() from None
            raise PlaidError(code if isinstance(code, str) else "PLAID_ERROR") from None
        except (URLError, TimeoutError, ValueError) as exc:
            raise PlaidError("PLAID_UNAVAILABLE") from exc
        if not isinstance(result, dict):
            raise PlaidError("INVALID_RESPONSE")
        return result
