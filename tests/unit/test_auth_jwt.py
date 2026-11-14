"""JWT issuance and verification rules (offline, no database)."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from services.backend.app.api import auth
from services.backend.app.api.auth import (
    InvalidToken,
    decode_access,
    decode_refresh,
    issue_access_token,
    issue_refresh_token,
)

SECRET = "unit-test-secret-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_access_roundtrip() -> None:
    user_id = uuid4()
    token, ttl = issue_access_token(user_id)
    assert ttl == 900
    assert decode_access(token) == user_id


def test_refresh_roundtrip_carries_jti() -> None:
    user_id = uuid4()
    token, jti, expires_at = issue_refresh_token(user_id)
    assert decode_refresh(token) == (user_id, jti)
    assert expires_at > datetime.now(UTC)


def test_token_claims_and_ttl() -> None:
    user_id = uuid4()
    access, _ = issue_access_token(user_id)
    claims = jwt.decode(access, SECRET, algorithms=["HS256"])
    assert claims["typ"] == "access" and claims["sub"] == str(user_id)
    assert claims["exp"] - claims["iat"] == 900
    refresh, jti, _ = issue_refresh_token(user_id)
    refresh_claims = jwt.decode(refresh, SECRET, algorithms=["HS256"])
    assert refresh_claims["typ"] == "refresh" and refresh_claims["jti"] == str(jti)
    assert refresh_claims["exp"] - refresh_claims["iat"] == 30 * 24 * 3600


def test_expired_access_rejected() -> None:
    past = datetime.now(UTC) - timedelta(seconds=10)
    token = jwt.encode(
        {"sub": str(uuid4()), "typ": "access", "exp": int(past.timestamp())}, SECRET, algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_access(token)


def test_tampered_signature_rejected() -> None:
    token, _ = issue_access_token(uuid4())
    header, payload, signature = token.split(".")
    flipped = "AAAA" if signature[-4:] != "AAAA" else "BBBB"
    with pytest.raises(InvalidToken):
        decode_access(".".join([header, payload, signature[:-4] + flipped]))


def test_refresh_used_as_access_rejected() -> None:
    token, _, _ = issue_refresh_token(uuid4())
    with pytest.raises(InvalidToken):
        decode_access(token)


def test_access_used_as_refresh_rejected() -> None:
    token, _ = issue_access_token(uuid4())
    with pytest.raises(InvalidToken):
        decode_refresh(token)


def test_alg_none_rejected() -> None:
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": str(uuid4()), "typ": "access",
        "exp": int((datetime.now(UTC) + timedelta(seconds=900)).timestamp()),
    }).encode())
    with pytest.raises(InvalidToken):
        decode_access(f"{header}.{payload}.")


def test_wrong_secret_rejected() -> None:
    token, _ = issue_access_token(uuid4())
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    forged = jwt.encode(claims, "a-different-secret-0123456789abcdef", algorithm="HS256")
    with pytest.raises(InvalidToken):
        decode_access(forged)


def test_missing_typ_rejected() -> None:
    token = jwt.encode(
        {"sub": str(uuid4()), "exp": int((datetime.now(UTC) + timedelta(seconds=900)).timestamp())},
        SECRET, algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_access(token)


def test_unset_secret_generates_random_per_process_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    monkeypatch.setattr(auth, "_fallback_secret", None)
    user_id = uuid4()
    with caplog.at_level(logging.WARNING):
        token, _ = issue_access_token(user_id)
    assert any("AUTH_JWT_SECRET is not set" in record.message for record in caplog.records)
    assert decode_access(token) == user_id
    # A freshly generated secret differs from the previous one: no hardcoded default.
    monkeypatch.setattr(auth, "_fallback_secret", None)
    issue_access_token(uuid4())
    with pytest.raises(InvalidToken):
        decode_access(token)