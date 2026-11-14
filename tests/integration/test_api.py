"""HTTP and Postgres acceptance for resumable trip runs."""

from __future__ import annotations

import json
import os
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from services.backend.app.api.auth import issue_access_token
from services.backend.app.api.dependencies import get_executor, get_store
from services.backend.app.api.executor import FakeRunExecutor, RunResult
from services.backend.app.api.schemas import SSEEvent
from services.backend.app.api.store import TripStore
from services.backend.app.main import create_app
from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState


def _app(executor: FakeRunExecutor | None = None) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_store] = lambda: TripStore()
    if executor is not None:
        app.dependency_overrides[get_executor] = lambda: executor
    return app


def _bearer() -> dict[str, str]:
    """Anonymous access was removed; every trip call now carries an authenticated owner."""
    return {"Authorization": f"Bearer {issue_access_token(uuid4())[0]}"}


@pytest.mark.asyncio
async def test_interrupt_survives_api_restart_and_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-api-0123456789abcdef0123456789")
    store = TripStore()
    try:
        connection = await asyncpg.connect(store.dsn)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    else:
        await connection.close()

    auth = _bearer()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        created = await client.post("/v1/trips", headers=auth)
        assert created.status_code == 201
        trip_id = UUID(created.json()["trip_id"])

    interrupted = TripState(trip_id=trip_id, version=1, run_status=RunStatus.INTERRUPTED,
                            awaiting_fields=["origin"], outcome=Outcome.AWAITING_ANSWER)
    first_executor = FakeRunExecutor([RunResult(interrupted, (SSEEvent(event="question", data={
        "field": "origin", "text": "Откуда?", "options": [], "allow_skip": False,
    }),))])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(first_executor)),
                                 base_url="http://test") as client:
        streamed = await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "Хочу на море"}, headers=auth)
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert "event: run_started" in streamed.text
        assert "event: question" in streamed.text
        first_data = next(line[6:] for line in streamed.text.splitlines() if line.startswith("data: "))
        run_id = UUID(json.loads(first_data)["run_id"])
        assert len(first_executor.calls) == 1

    # New app and executor instance stand in for a restarted API process.
    completed = TripState(trip_id=trip_id, version=2, run_status=RunStatus.COMPLETED,
                          outcome=Outcome.BEST_EFFORT)
    resumed_executor = FakeRunExecutor([RunResult(completed, (SSEEvent(event="answer_text", data={
        "block_id": "answer", "text": "Принято", "numbers_verified": True,
    }),))])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(resumed_executor)),
                                 base_url="http://test") as client:
        trace = await client.get(f"/v1/runs/{run_id}", headers=auth)
        assert trace.json()["status"] == "interrupted"
        assert trace.json()["awaiting"] == ["origin"]
        state = await client.get(f"/v1/trips/{trip_id}/state", headers=auth)
        assert state.json()["run_status"] == "interrupted"

        answer = await client.post(f"/v1/runs/{run_id}/answers",
                                   json={"field": "origin", "value": "Москва"}, headers=auth)
        assert answer.status_code == 200
        assert answer.json()["status"] == "completed"
        duplicate = await client.post(f"/v1/runs/{run_id}/answers",
                                      json={"field": "origin", "value": "Москва"}, headers=auth)
        assert duplicate.status_code == 200
        assert duplicate.json() == answer.json()
        assert len(resumed_executor.calls) == 1
        conflict = await client.post(f"/v1/runs/{run_id}/answers",
                                     json={"field": "origin", "value": "Питер"}, headers=auth)
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "Ответ на это поле уже сохранён с другим значением"
        assert (await client.get(f"/v1/trips/{trip_id}/state", headers=auth)).json()["version"] == 2
        snapshot = await client.get(f"/v1/trips/{trip_id}/state?version=1", headers=auth)
        assert snapshot.json()["run_status"] == "interrupted"

        checkout = await client.post(f"/v1/trips/{trip_id}/checkout", headers={"Idempotency-Key": "buy-1", **auth})
        replay = await client.post(f"/v1/trips/{trip_id}/checkout", headers={"Idempotency-Key": "buy-1", **auth})
        assert checkout.status_code == replay.status_code == 200
        assert checkout.json()["order_id"] == replay.json()["order_id"]
        assert checkout.json()["idempotent_replay"] is False
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["simulated"] is True
        invalid = await client.get("/v1/trips/not-a-uuid/state", headers=auth)
        assert invalid.status_code == 422
        assert invalid.json()["detail"] == "Некорректные параметры запроса"


@pytest.mark.asyncio
async def test_agent_executor_plans_across_api_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.agent.app.llm.slots import MockSlots
    from services.agent.app.tools.seed_sources import seed_sources
    from services.backend.app.api.agent_executor import AgentRunExecutor

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-api-0123456789abcdef0123456789")
    store = TripStore()
    try:
        connection = await asyncpg.connect(store.dsn)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    else:
        await connection.close()

    def agent_app() -> FastAPI:
        app = create_app()
        app.dependency_overrides[get_store] = lambda: TripStore()
        executor = AgentRunExecutor(seed_sources(), slots=MockSlots())
        app.dependency_overrides[get_executor] = lambda: executor
        return app

    auth = _bearer()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app()), base_url="http://test") as client:
        trip_id = (await client.post("/v1/trips", headers=auth)).json()["trip_id"]
        streamed = await client.post(f"/v1/trips/{trip_id}/messages", json={
            "text": "хочу к тёплому морю в ноябре вдвоём с ребёнком, 200к, без долгих перелётов",
        }, headers=auth)
        assert "event: question" in streamed.text
        payload = next(line[6:] for line in streamed.text.splitlines() if line.startswith("data: "))
        run_id = json.loads(payload)["run_id"]

    # Новый процесс API: ход агента восстанавливается из runs.agent_run, а не из памяти.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app()), base_url="http://test") as client:
        origin = await client.post(f"/v1/runs/{run_id}/answers", json={"field": "origin", "value": "Москва"},
                                   headers=auth)
        assert origin.status_code == 200 and origin.json()["awaiting"] == ["dates.start"]
        unreadable = await client.post(f"/v1/runs/{run_id}/answers",
                                       json={"field": "dates.start", "value": "когда-нибудь"}, headers=auth)
        assert unreadable.status_code == 409
        done = await client.post(f"/v1/runs/{run_id}/answers", json={"field": "dates.start", "value": "с 14 по 21"},
                                 headers=auth)
        assert done.status_code == 200 and done.json()["status"] == "completed"
        events = [e["event"] for e in done.json()["events"]]
        assert events[0] == "run_started" and "plan_updated" in events and "answer_text" in events
        state = (await client.get(f"/v1/trips/{trip_id}/state", headers=auth)).json()
        assert state["outcome"] == "plan_ready" and state["version"] == 3
        assert state["chosen"]["component_refs"]["destination"]


@pytest.mark.asyncio
async def test_orders_are_saved_per_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-api-0123456789abcdef0123456789")
    token = "123456:test-token"
    user_id = int(time.time() * 1000)
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "Аня"})}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    signed = urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})

    store = TripStore()
    try:
        connection = await asyncpg.connect(store.dsn)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    else:
        await connection.close()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    telegram = {"Authorization": f"tma {signed}"}
    # The anonymous X-Client-Id path was removed; a second authenticated user
    # (a different JWT subject) stands in for "someone else's browser".
    other = _bearer()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        trip_id = (await client.post("/v1/trips", headers=telegram)).json()["trip_id"]
        placed = await client.post(f"/v1/trips/{trip_id}/checkout",
                                   headers={"Idempotency-Key": "k1", **telegram})
        assert placed.status_code == 200
        replay = await client.post(f"/v1/trips/{trip_id}/checkout",
                                   headers={"Idempotency-Key": "k1", **telegram})
        assert replay.json()["order_id"] == placed.json()["order_id"]

        mine = await client.get("/v1/orders", headers=telegram)
        assert mine.status_code == 200
        assert [o["order_id"] for o in mine.json()] == [placed.json()["order_id"]]
        assert mine.json()[0]["trip_id"] == trip_id
        assert mine.json()[0]["snapshot"]["lines"] == []

        assert (await client.get("/v1/orders", headers=other)).json() == []
        assert (await client.get("/v1/orders")).status_code == 401
        forged = {"Authorization": telegram["Authorization"].replace("hash=", "hash=0")}
        assert (await client.get("/v1/orders", headers=forged)).status_code == 401