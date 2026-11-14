"""Liveness and readiness probes for the compose healthcheck and nginx.

``/health`` answers without touching Postgres so a database outage cannot make the
orchestrator restart a perfectly alive API; ``/health/ready`` acquires a pooled
connection and only then reports ready, which is what nginx should wait for.
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .store import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> JSONResponse:
    try:
        pool = await get_pool()
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ok"})