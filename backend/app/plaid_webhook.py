"""Validate signed Plaid webhook bodies before processing them."""

import hashlib
import hmac
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import jwt


class InvalidWebhook(ValueError):
    """The request is not a valid, recent Plaid webhook."""


def verify_webhook(
    token: str,
    body: bytes,
    get_key: Callable[[str], Mapping[str, Any]],
    *,
    now: float | None = None,
) -> None:
    """Verify ES256, Plaid's key, issuance time, and the exact body bytes.

    Key retrieval failures propagate, so the HTTP handler can return a retryable
    server error rather than treating an upstream outage as a forged request.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise InvalidWebhook("Invalid signature header") from exc
    kid = header.get("kid")
    if header.get("alg") != "ES256" or not isinstance(kid, str) or not kid or len(kid) > 256:
        raise InvalidWebhook("Invalid signature header")
    key = get_key(kid)
    if (key.get("kid") != kid or key.get("alg") != "ES256" or key.get("kty") != "EC"
            or key.get("crv") != "P-256" or key.get("use") != "sig"
            or key.get("expired_at") is not None):
        raise InvalidWebhook("Invalid verification key")
    try:
        public_key = jwt.PyJWK.from_dict(dict(key), algorithm="ES256").key
        claims = jwt.decode(token, public_key, algorithms=["ES256"],
                            options={"require": ["iat", "request_body_sha256"],
                                     "verify_iat": False, "verify_aud": False})
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise InvalidWebhook("Invalid webhook signature") from exc
    issued = claims.get("iat")
    current = time.time() if now is None else now
    if isinstance(issued, bool) or not isinstance(issued, (int, float)) or not 0 <= current - issued <= 300:
        raise InvalidWebhook("Invalid webhook timestamp")
    expected = claims.get("request_body_sha256")
    if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), expected)):
        raise InvalidWebhook("Invalid webhook body")
