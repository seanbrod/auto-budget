import hashlib
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.plaid_webhook import InvalidWebhook, verify_webhook


@pytest.fixture
def signed():
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": "key-1", "alg": "ES256", "use": "sig", "expired_at": None})

    def make(body=b'{"item_id":"one"}', issued=1000, key=private):
        claims = {"iat": issued, "request_body_sha256": hashlib.sha256(body).hexdigest()}
        return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "key-1"})

    return jwk, make


def test_verifies_valid_raw_body(signed):
    key, make = signed
    verify_webhook(make(), b'{"item_id":"one"}', lambda kid: key, now=1050)


def test_rejects_tampering_stale_future_and_forged_signatures(signed):
    key, make = signed
    other = ec.generate_private_key(ec.SECP256R1())
    for token, body, now in [(make(), b'{ "item_id":"one"}', 1050),
                             (make(), b'{"item_id":"one"}', 1301),
                             (make(issued=1001), b'{"item_id":"one"}', 1000),
                             (make(key=other), b'{"item_id":"one"}', 1050)]:
        with pytest.raises(InvalidWebhook):
            verify_webhook(token, body, lambda kid: key, now=now)


def test_rejects_wrong_or_expired_key(signed):
    key, make = signed
    for change in ({"kid": "other"}, {"expired_at": 900}, {"alg": "HS256"}):
        with pytest.raises(InvalidWebhook):
            verify_webhook(make(), b'{"item_id":"one"}', lambda kid, c=change: key | c, now=1050)


def test_key_service_failure_is_retryable(signed):
    _, make = signed

    def unavailable(_kid):
        raise RuntimeError("key service unavailable")

    with pytest.raises(RuntimeError):
        verify_webhook(make(), b'{"item_id":"one"}', unavailable, now=1050)
