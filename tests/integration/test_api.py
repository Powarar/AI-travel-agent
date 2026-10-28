"""HTTP and Postgres acceptance for resumable trip runs."""

from __future__ import annotations

import json
import os
from uuid import UUID

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from services.backend.app.api.dependencies import get_executor, get_store
from services.backend.app.api.executor import FakeRunExecutor, RunResult
from services.backend.app.main import create_app
from services.backend.app.api.schemas import SSEEvent
from services.backend.app.api.store import TripStore
from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState


def _app(executor: FakeRunExecutor | None = None) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_store] = lambda: TripStore()
    if executor is not None:
        app.dependency_overrides[get_executor] = lambda: executor
    return app


@pytest.mark.asyncio
async def test_interrupt_survives_api_restart_and_idempotency() -> None:
    store = TripStore()
    try:
        connection = await asyncpg.connect(store.dsn)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    else:
        await connection.close()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        created = await client.post("/v1/trips")
        assert created.status_code == 201
        trip_id = UUID(created.json()["trip_id"])

    interrupted = TripState(trip_id=trip_id, version=1, run_status=RunStatus.INTERRUPTED,
                            awaiting_fields=["origin"], outcome=Outcome.AWAITING_ANSWER)
    first_executor = FakeRunExecutor([RunResult(interrupted, (SSEEvent(event="question", data={
        "field": "origin", "text": "Откуда?", "options": [], "allow_skip": False,
    }),))])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(first_executor)), base_url="http://test") as client:
        streamed = await client.post(f"/v1/trips/{trip_id}/messages", json={"text": "Хочу на море"})
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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(resumed_executor)), base_url="http://test") as client:
        trace = await client.get(f"/v1/runs/{run_id}")
        assert trace.json()["status"] == "interrupted"
        assert trace.json()["awaiting"] == ["origin"]
        state = await client.get(f"/v1/trips/{trip_id}/state")
        assert state.json()["run_status"] == "interrupted"

        answer = await client.post(f"/v1/runs/{run_id}/answers",
                                   json={"field": "origin", "value": "Москва"})
        assert answer.status_code == 200
        assert answer.json()["status"] == "completed"
        duplicate = await client.post(f"/v1/runs/{run_id}/answers",
                                      json={"field": "origin", "value": "Москва"})
        assert duplicate.status_code == 200
        assert duplicate.json() == answer.json()
        assert len(resumed_executor.calls) == 1
        conflict = await client.post(f"/v1/runs/{run_id}/answers",
                                     json={"field": "origin", "value": "Питер"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "Ответ на это поле уже сохранён с другим значением"
        assert (await client.get(f"/v1/trips/{trip_id}/state")).json()["version"] == 2
        assert (await client.get(f"/v1/trips/{trip_id}/state?version=1")).json()["run_status"] == "interrupted"

        checkout = await client.post(f"/v1/trips/{trip_id}/checkout", headers={"Idempotency-Key": "buy-1"})
        replay = await client.post(f"/v1/trips/{trip_id}/checkout", headers={"Idempotency-Key": "buy-1"})
        assert checkout.status_code == replay.status_code == 200
        assert checkout.json()["order_id"] == replay.json()["order_id"]
        assert checkout.json()["idempotent_replay"] is False
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["simulated"] is True
        invalid = await client.get("/v1/trips/not-a-uuid/state")
        assert invalid.status_code == 422
        assert invalid.json()["detail"] == "Некорректные параметры запроса"
