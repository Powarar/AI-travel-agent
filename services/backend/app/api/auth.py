"""Password hashing, JWT issuance/verification and credential policy.

This module is deliberately free of database and HTTP imports so the password and
token rules can be unit-tested offline. Persistence and routing live in
``app.api.v1.auth``.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

ACCESS_TTL_S = 900
REFRESH_TTL_S = 30 * 24 * 3600
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128
_ALGORITHM = "HS256"

_hasher = PasswordHasher()

#: `email-validator` is not a project dependency, so a documented strict regex is
#: used instead of pydantic's EmailStr. It is applied to the already lowercased and
#: trimmed address: a dot-atom local part, no consecutive dots, and a dotted domain
#: whose final label is at least two letters. Internationalised domains are out of
#: scope for this product.
_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$"
)

_fallback_secret: str | None = None


class InvalidToken(Exception):
    """A JWT is missing, malformed, expired, mis-signed or of the wrong type."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """Validate an already-normalized address against the documented regex."""
    return len(email) <= 254 and _EMAIL_RE.match(email) is not None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time argon2id verification; any failure is a plain ``False``."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _secret() -> str:
    secret = os.environ.get("AUTH_JWT_SECRET")
    if secret:
        return secret
    global _fallback_secret
    if _fallback_secret is None:
        _fallback_secret = secrets.token_urlsafe(48)
        logger.warning(
            "AUTH_JWT_SECRET is not set; generated a random per-process secret. "
            "Tokens will not survive a restart and will not be shared across workers."
        )
    return _fallback_secret


def _encode(subject: str, typ: Literal["access", "refresh"], ttl_s: int, jti: UUID | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
    }
    if jti is not None:
        payload["jti"] = str(jti)
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def _decode(token: str, expected_typ: Literal["access", "refresh"]) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _secret(), algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken("token rejected") from exc
    if payload.get("typ") != expected_typ:
        raise InvalidToken("wrong token type")
    return payload


def issue_access_token(user_id: UUID) -> tuple[str, int]:
    return _encode(str(user_id), "access", ACCESS_TTL_S), ACCESS_TTL_S


def issue_refresh_token(user_id: UUID) -> tuple[str, UUID, datetime]:
    jti = uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=REFRESH_TTL_S)
    return _encode(str(user_id), "refresh", REFRESH_TTL_S, jti=jti), jti, expires_at


def decode_access(token: str) -> UUID:
    payload = _decode(token, "access")
    try:
        return UUID(str(payload["sub"]))
    except (ValueError, KeyError) as exc:
        raise InvalidToken("bad subject") from exc


def decode_refresh(token: str) -> tuple[UUID, UUID]:
    payload = _decode(token, "refresh")
    try:
        return UUID(str(payload["sub"])), UUID(str(payload["jti"]))
    except (ValueError, KeyError) as exc:
        raise InvalidToken("bad subject or jti") from exc