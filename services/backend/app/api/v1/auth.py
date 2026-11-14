"""Registration, login, rotating refresh tokens and the current-user endpoint.

Error contract: FastAPI ``{"detail": "<русский текст>"}`` with 401 (bad
credentials/token), 409 (email taken), 422 (validation) and 429 (lockout). Login
failure is identical for an unknown email and a wrong password, and a dummy
argon2 verification equalizes timing so accounts cannot be enumerated.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..auth import (
    InvalidToken,
    decode_access,
    decode_refresh,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_password,
)
from ..schemas import AuthUser, CredentialsIn, LogoutIn, MeOut, RefreshIn, TokenResponse
from ..store import get_pool

router = APIRouter()

_BAD_CREDENTIALS = "Неверный email или пароль"
_BAD_TOKEN = "Недействительный или просроченный токен"
_EMAIL_TAKEN = "Email уже зарегистрирован"
_TOO_MANY_ATTEMPTS = "Слишком много попыток входа. Попробуйте позже."

#: A real argon2id hash verified against unknown emails so the response time of a
#: missing account matches that of a wrong password (no timing-based enumeration).
_DUMMY_HASH = hash_password("timing-equalization-dummy-password")


@dataclass
class _Attempts:
    count: int
    window_start: float
    locked_until: float


class LoginThrottle:
    """In-process per-email AND per-IP failed-login lockout.

    Limitation: the counters live in this process only, so they are not shared
    across workers or replicas. nginx also IP-limits these endpoints in
    production; a shared store (e.g. Redis) would be required for a global
    lockout. The clock is injectable so the window can be tested deterministically.
    """

    def __init__(self, max_attempts: int = 5, window_s: float = 300.0,
                 lockout_s: float = 900.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._max = max_attempts
        self._window = window_s
        self._lockout = lockout_s
        self._clock = clock
        self._by_email: dict[str, _Attempts] = {}
        self._by_ip: dict[str, _Attempts] = {}

    def _prune(self, bucket: dict[str, _Attempts], key: str, now: float) -> _Attempts | None:
        state = bucket.get(key)
        if state is None:
            return None
        if state.locked_until:
            if now >= state.locked_until:
                del bucket[key]
                return None
            return state
        if now - state.window_start > self._window:
            del bucket[key]
            return None
        return state

    def allowed(self, email: str, ip: str) -> bool:
        now = self._clock()
        for bucket, key in ((self._by_email, email), (self._by_ip, ip)):
            state = self._prune(bucket, key, now)
            if state is not None and state.locked_until > now:
                return False
        return True

    def record_failure(self, email: str, ip: str) -> None:
        now = self._clock()
        for bucket, key in ((self._by_email, email), (self._by_ip, ip)):
            state = self._prune(bucket, key, now)
            if state is None:
                state = _Attempts(count=0, window_start=now, locked_until=0.0)
                bucket[key] = state
            state.count += 1
            if state.count >= self._max:
                state.locked_until = now + self._lockout

    def record_success(self, email: str, ip: str) -> None:
        self._by_email.pop(email, None)
        self._by_ip.pop(ip, None)

    def reset(self) -> None:
        self._by_email.clear()
        self._by_ip.clear()


throttle = LoginThrottle(
    max_attempts=int(os.environ.get("AUTH_LOGIN_MAX_ATTEMPTS", "5")),
    window_s=float(os.environ.get("AUTH_LOGIN_WINDOW_S", "300")),
    lockout_s=float(os.environ.get("AUTH_LOGIN_LOCKOUT_S", "900")),
)


async def _issue(conn: asyncpg.Connection, user_id: UUID, email: str) -> TokenResponse:
    access_token, expires_in = issue_access_token(user_id)
    refresh_token, jti, expires_at = issue_refresh_token(user_id)
    await conn.execute(
        "INSERT INTO refresh_tokens(jti, user_id, expires_at) VALUES($1, $2, $3)", jti, user_id, expires_at,
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token,
                         expires_in=expires_in, user=AuthUser(id=user_id, email=email))


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
async def register(body: CredentialsIn) -> TokenResponse:
    user_id = uuid4()
    password_hash = hash_password(body.password)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        try:
            await conn.execute(
                "INSERT INTO users(id, email, password_hash) VALUES($1, $2, $3)",
                user_id, body.email, password_hash,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(status_code=409, detail=_EMAIL_TAKEN) from exc
        return await _issue(conn, user_id, body.email)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: CredentialsIn, request: Request) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    email = body.email
    if not throttle.allowed(email, ip):
        raise HTTPException(status_code=429, detail=_TOO_MANY_ATTEMPTS)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, password_hash FROM users WHERE email=$1", email)
        if row is None:
            verify_password(_DUMMY_HASH, body.password)
            throttle.record_failure(email, ip)
            raise HTTPException(status_code=401, detail=_BAD_CREDENTIALS)
        if not verify_password(row["password_hash"], body.password):
            throttle.record_failure(email, ip)
            raise HTTPException(status_code=401, detail=_BAD_CREDENTIALS)
        throttle.record_success(email, ip)
        async with conn.transaction():
            return await _issue(conn, row["id"], email)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(body: RefreshIn) -> TokenResponse:
    try:
        _, jti = decode_refresh(body.refresh_token)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=_BAD_TOKEN) from exc
    pool = await get_pool()
    async with pool.acquire() as conn:
        result: TokenResponse | None = None
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT user_id, expires_at, revoked_at FROM refresh_tokens WHERE jti=$1 FOR UPDATE", jti,
            )
            if row is not None and row["revoked_at"] is not None:
                # Reuse of a rotated token compromises the session: revoke the whole
                # family. The schema has no lineage column, so the family is every
                # refresh token belonging to this user. Committed before the 401.
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at=now() WHERE user_id=$1 AND revoked_at IS NULL",
                    row["user_id"],
                )
            elif row is not None and row["expires_at"] > datetime.now(UTC):
                await conn.execute("UPDATE refresh_tokens SET revoked_at=now() WHERE jti=$1", jti)
                email = await conn.fetchval("SELECT email FROM users WHERE id=$1", row["user_id"])
                if email is not None:
                    result = await _issue(conn, row["user_id"], email)
        if result is None:
            raise HTTPException(status_code=401, detail=_BAD_TOKEN)
        return result


@router.post("/auth/logout", status_code=204, response_class=Response)
async def logout(body: LogoutIn) -> Response:
    try:
        _, jti = decode_refresh(body.refresh_token)
    except InvalidToken:
        return Response(status_code=204)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE refresh_tokens SET revoked_at=now() WHERE jti=$1 AND revoked_at IS NULL", jti,
        )
    return Response(status_code=204)


@router.get("/auth/me", response_model=MeOut)
async def me(authorization: str | None = Header(default=None)) -> MeOut:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail=_BAD_TOKEN)
    try:
        user_id = decode_access(token)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=_BAD_TOKEN) from exc
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, created_at, telegram_id FROM users WHERE id=$1", user_id)
    if row is None:
        raise HTTPException(status_code=401, detail=_BAD_TOKEN)
    return MeOut(id=row["id"], email=row["email"], created_at=row["created_at"],
                 telegram_linked=row["telegram_id"] is not None)