"""Health probes: liveness without Postgres, readiness through the shared pool."""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest

from services.backend.app.api import health
from services.backend.app.main import create_app


@pytest.mark.asyncio
async def test_liveness_does_not_need_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_pool() -> asyncpg.Pool:
        raise OSError("database is down")

    monkeypatch.setattr(health, "get_pool", no_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        alive = await client.get("/health")
        assert alive.status_code == 200
        assert alive.json() == {"status": "ok"}
        ready = await client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json() == {"status": "unavailable"}


@pytest.mark.asyncio
async def test_readiness_reports_a_live_database() -> None:
    from services.backend.app.api.store import get_pool

    try:
        pool = await get_pool()
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok"}