"""Auth API acceptance against real Postgres: register/login/me, rotation, lockout."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from services.backend.app.api.v1 import auth as auth_router
from services.backend.app.main import create_app

PASSWORD = "supersecret1"
SECRET = "integration-test-secret-0123456789abcdef"


def _email() -> str:
    return f"user-{uuid4().hex}@example.com"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    auth_router.throttle.reset()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c


def _assert_token_response(body: dict[str, object], email: str) -> None:
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert isinstance(body["access_token"], str) and isinstance(body["refresh_token"], str)
    assert body["user"]["email"] == email  # type: ignore[index]
    assert body["user"]["id"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_register_login_me_happy_path(client: httpx.AsyncClient) -> None:
    email = _email()
    registered = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert registered.status_code == 201
    _assert_token_response(registered.json(), email)

    logged_in = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert logged_in.status_code == 200
    _assert_token_response(logged_in.json(), email)
    assert logged_in.json()["user"]["id"] == registered.json()["user"]["id"]

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"})
    assert me.status_code == 200
    body = me.json()
    assert set(body) == {"id", "email", "created_at", "telegram_linked"}
    assert body["email"] == email
    assert body["id"] == registered.json()["user"]["id"]
    assert body["telegram_linked"] is False


@pytest.mark.asyncio
async def test_duplicate_email_conflict(client: httpx.AsyncClient) -> None:
    email = _email()
    assert (await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})).status_code == 201
    again = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert again.status_code == 409
    assert again.json()["detail"] == "Email уже зарегистрирован"
    # Normalization means a case/whitespace variant is the same account.
    variant = await client.post("/v1/auth/register", json={"email": f"  {email.upper()}  ", "password": PASSWORD})
    assert variant.status_code == 409


@pytest.mark.asyncio
async def test_weak_password_and_invalid_email_are_422(client: httpx.AsyncClient) -> None:
    short = await client.post("/v1/auth/register", json={"email": _email(), "password": "short"})
    assert short.status_code == 422
    assert short.json()["detail"] == "Некорректные параметры запроса"
    long = await client.post("/v1/auth/register", json={"email": _email(), "password": "x" * 129})
    assert long.status_code == 422
    bad_email = await client.post("/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD})
    assert bad_email.status_code == 422


@pytest.mark.asyncio
async def test_email_case_and_whitespace_normalization(client: httpx.AsyncClient) -> None:
    email = _email()
    registered = await client.post("/v1/auth/register", json={"email": f"  {email.upper()}  ", "password": PASSWORD})
    assert registered.status_code == 201
    assert registered.json()["user"]["email"] == email
    logged_in = await client.post("/v1/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["email"] == email


@pytest.mark.asyncio
async def test_login_unknown_email_and_wrong_password_are_identical_401(client: httpx.AsyncClient) -> None:
    email = _email()
    await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    unknown = await client.post("/v1/auth/login", json={"email": _email(), "password": PASSWORD})
    wrong = await client.post("/v1/auth/login", json={"email": email, "password": "wrongpassword1"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.json()["detail"] == "Неверный email или пароль"


@pytest.mark.asyncio
async def test_unknown_email_still_runs_a_password_verification(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Timing equalization: a missing account must still perform an argon2 verify so
    # it is not distinguishable from a wrong password by response time.
    seen: list[str] = []
    real = auth_router.verify_password

    def spy(password_hash: str, password: str) -> bool:
        seen.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr(auth_router, "verify_password", spy)
    response = await client.post("/v1/auth/login", json={"email": _email(), "password": PASSWORD})
    assert response.status_code == 401
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_rejected(client: httpx.AsyncClient) -> None:
    email = _email()
    registered = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    first_refresh = registered.json()["refresh_token"]
    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    _assert_token_response(rotated.json(), email)
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh
    # The new token works; the old one is rejected after use. (Replaying the old
    # token is reuse and revokes the family, so the new token is checked first.)
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": second_refresh})).status_code == 200
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})).status_code == 401


@pytest.mark.asyncio
async def test_reuse_of_rotated_token_revokes_the_family(client: httpx.AsyncClient) -> None:
    email = _email()
    registered = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    first_refresh = registered.json()["refresh_token"]
    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
    second_refresh = rotated.json()["refresh_token"]
    # Replaying the already-rotated token is reuse: it must fail and revoke everything.
    reuse = await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert reuse.status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": second_refresh})).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_and_is_idempotent(client: httpx.AsyncClient) -> None:
    email = _email()
    registered = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    refresh_token = registered.json()["refresh_token"]
    out = await client.post("/v1/auth/logout", json={"refresh_token": refresh_token})
    assert out.status_code == 204
    assert out.content == b""
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})).status_code == 401
    again = await client.post("/v1/auth/logout", json={"refresh_token": refresh_token})
    assert again.status_code == 204
    assert again.content == b""


@pytest.mark.asyncio
async def test_refresh_and_me_reject_invalid_tokens(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": "garbage"})).status_code == 401
    assert (await client.get("/v1/auth/me")).status_code == 401
    assert (await client.get("/v1/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401
    registered = await client.post("/v1/auth/register", json={"email": _email(), "password": PASSWORD})
    # A refresh token is not an access token.
    refresh_as_access = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {registered.json()['refresh_token']}"})
    assert refresh_as_access.status_code == 401


@pytest.mark.asyncio
async def test_login_lockout_returns_429_and_recovers_after_window(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(auth_router, "throttle", auth_router.LoginThrottle(
        max_attempts=3, window_s=60.0, lockout_s=30.0, clock=lambda: clock[0]))
    email = _email()
    await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    for _ in range(3):
        wrong = await client.post("/v1/auth/login", json={"email": email, "password": "wrongpassword1"})
        assert wrong.status_code == 401
    locked = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["detail"] == "Слишком много попыток входа. Попробуйте позже."
    clock[0] = 31.0
    recovered = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert recovered.status_code == 200


def test_throttle_locks_per_email_and_per_ip_and_recovers() -> None:
    clock = [0.0]
    by_email = auth_router.LoginThrottle(max_attempts=3, window_s=60.0, lockout_s=30.0, clock=lambda: clock[0])
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        by_email.record_failure("victim@example.com", ip)
    assert not by_email.allowed("victim@example.com", "9.9.9.9")

    by_ip = auth_router.LoginThrottle(max_attempts=3, window_s=60.0, lockout_s=30.0, clock=lambda: clock[0])
    for email in ("a@example.com", "b@example.com", "c@example.com"):
        by_ip.record_failure(email, "5.5.5.5")
    assert not by_ip.allowed("fresh@example.com", "5.5.5.5")

    clock[0] = 31.0
    assert by_email.allowed("victim@example.com", "9.9.9.9")
    assert by_ip.allowed("fresh@example.com", "5.5.5.5")