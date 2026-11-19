"""Shared fixtures for Postgres-backed integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from services.backend.app.api.store import close_pool


@pytest_asyncio.fixture(autouse=True)
async def _release_pool() -> AsyncIterator[None]:
    """Close the shared pool in the test's own loop so the next case starts clean.

    Each pytest-asyncio case runs in a fresh event loop; a pool bound to a closed
    loop cannot be reused, so it is gracefully closed here rather than left stale.
    """
    yield
    await close_pool()