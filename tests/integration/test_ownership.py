"""Ownership enforcement: IDOR returns 404, and every trip endpoint requires auth."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.backend.app.api.dependencies import get_executor, get_store
from services.backend.app.api.executor import FixtureRunExecutor
from services.backend.app.api.store import TripStore
from services.backend.app.main import create_app

PASSWORD = "supersecret1"
SECRET = "ownership-test-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)


def _app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_store] = lambda: TripStore()
    app.dependency_overrides[get_executor] = lambda: FixtureRunExecutor()
    return app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as c:
        yield c


async def _register(client: httpx.AsyncClient) -> dict[str, str]:
    email = f"owner-{uuid4().hex}@example.com"
    response = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _run_id(streamed: httpx.Response) -> UUID:
    first = next(line[6:] for line in streamed.text.splitlines() if line.startswith("data: "))
    return UUID(json.loads(first)["run_id"])


@pytest.mark.asyncio
async def test_user_cannot_read_or_modify_another_users_trip(client: httpx.AsyncClient) -> None:
    alice = await _register(client)
    bob = await _register(client)

    created = await client.post("/v1/trips", headers=alice)
    assert created.status_code == 201
    trip_id = created.json()["trip_id"]
    streamed = await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "Хочу на море"}, headers=alice)
    assert streamed.status_code == 200
    run_id = _run_id(streamed)

    # Alice can reach her own trip and run.
    assert (await client.get(f"/v1/trips/{trip_id}/state", headers=alice)).status_code == 200
    assert (await client.get(f"/v1/runs/{run_id}", headers=alice)).status_code == 200

    # Bob gets 404 (not 403) everywhere, so existence is not leaked.
    assert (await client.get(f"/v1/trips/{trip_id}/state", headers=bob)).status_code == 404
    assert (await client.get(f"/v1/trips/{trip_id}/state?version=0", headers=bob)).status_code == 404
    assert (await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "взлом"}, headers=bob)).status_code == 404
    assert (await client.post(f"/v1/trips/{trip_id}/checkout",
                              headers={"Idempotency-Key": "k", **bob})).status_code == 404
    assert (await client.get(f"/v1/runs/{run_id}", headers=bob)).status_code == 404
    assert (await client.post(f"/v1/runs/{run_id}/answers",
                              json={"field": "origin", "value": "Москва"}, headers=bob)).status_code == 404

    # A non-existent trip is also 404 for the owner: indistinguishable from someone else's.
    assert (await client.get(f"/v1/trips/{uuid4()}/state", headers=alice)).status_code == 404


@pytest.mark.asyncio
async def test_orders_are_isolated_per_owner(client: httpx.AsyncClient) -> None:
    alice = await _register(client)
    bob = await _register(client)

    alice_trip = (await client.post("/v1/trips", headers=alice)).json()["trip_id"]
    bob_trip = (await client.post("/v1/trips", headers=bob)).json()["trip_id"]
    alice_order = (await client.post(f"/v1/trips/{alice_trip}/checkout",
                                     headers={"Idempotency-Key": "a", **alice})).json()["order_id"]
    bob_order = (await client.post(f"/v1/trips/{bob_trip}/checkout",
                                   headers={"Idempotency-Key": "b", **bob})).json()["order_id"]

    alice_orders = (await client.get("/v1/orders", headers=alice)).json()
    bob_orders = (await client.get("/v1/orders", headers=bob)).json()
    assert [o["order_id"] for o in alice_orders] == [alice_order]
    assert [o["order_id"] for o in bob_orders] == [bob_order]
    assert bob_order not in {o["order_id"] for o in alice_orders}
    assert alice_order not in {o["order_id"] for o in bob_orders}


@pytest.mark.asyncio
async def test_every_trip_endpoint_requires_authentication(client: httpx.AsyncClient) -> None:
    alice = await _register(client)
    trip_id = (await client.post("/v1/trips", headers=alice)).json()["trip_id"]
    streamed = await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "привет"}, headers=alice)
    run_id = _run_id(streamed)

    assert (await client.post("/v1/trips")).status_code == 401
    assert (await client.get(f"/v1/trips/{trip_id}/state")).status_code == 401
    assert (await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "привет"})).status_code == 401
    assert (await client.post(f"/v1/trips/{trip_id}/checkout", headers={"Idempotency-Key": "k"})).status_code == 401
    assert (await client.get(f"/v1/runs/{run_id}")).status_code == 401
    denied = await client.post(f"/v1/runs/{run_id}/answers", json={"field": "origin", "value": "Москва"})
    assert denied.status_code == 401
    assert (await client.get("/v1/orders")).status_code == 401
    # The removed anonymous path no longer grants access.
    assert (await client.get("/v1/orders", headers={"X-Client-Id": str(uuid4())})).status_code == 401