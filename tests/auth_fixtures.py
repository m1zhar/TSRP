"""Deterministic local-only bearer tokens for API tests."""

import base64
import hashlib
import hmac
import json
import time

API_TEST_SECRET = "test-api-secret-which-is-not-a-real-secret-0123456789"
API_TEST_SUBJECT = "a" * 64


def test_api_token(*, now=None, subject=API_TEST_SUBJECT, claims=None):
    issued = int(time.time() if now is None else now)
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
    header = encode({"alg": "HS256", "typ": "JWT"})
    payload = {"iss": "tsrp-web", "aud": "tsrp-api", "sub": subject, "iat": issued, "exp": issued + 60}
    if claims is not None:
        payload.update(claims)
    claims_part = encode(payload)
    signed = f"{header}.{claims_part}"
    signature = hmac.new(API_TEST_SECRET.encode(), signed.encode("ascii"), hashlib.sha256).digest()
    return f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
