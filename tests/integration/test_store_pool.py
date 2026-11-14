"""Shared pool reuse, same-trip serialization, cross-trip parallelism, and the
guarantee that no row lock is held while the executor runs. Real Postgres, fake
executors only; concurrency is proven with barriers/CAS outcomes, not wall-clock.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from services.backend.app.api.executor import RunInput, RunResult
from services.backend.app.api.store import Conflict, TripStore, close_pool, get_pool
from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState


async def _require_db() -> None:
    try:
        conn = await asyncpg.connect(TripStore().dsn)
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    await conn.close()


def _owner() -> str:
    return f"user:{uuid4()}"


class _BumpExecutor:
    """Returns the next version of whatever state it is given; yields to overlap."""

    def __init__(self) -> None:
        self.calls = 0

    async def aexecute(self, state: TripState, turn: RunInput) -> RunResult:
        self.calls += 1
        await asyncio.sleep(0)
        return RunResult(state.next_version().model_copy(update={
            "run_status": RunStatus.COMPLETED, "outcome": Outcome.BEST_EFFORT,
        }), ())


class _BarrierExecutor:
    def __init__(self, barrier: asyncio.Barrier) -> None:
        self._barrier = barrier

    async def aexecute(self, state: TripState, turn: RunInput) -> RunResult:
        await self._barrier.wait()
        return RunResult(state.next_version(), ())


class _SlowExecutor:
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self._entered = entered
        self._release = release

    async def aexecute(self, state: TripState, turn: RunInput) -> RunResult:
        self._entered.set()
        await self._release.wait()
        return RunResult(state.next_version(), ())


@pytest.mark.asyncio
async def test_pool_defaults_and_concurrent_requests_reuse_connections() -> None:
    await _require_db()
    pool = await get_pool()
    assert pool.get_min_size() == 2
    assert pool.get_max_size() == 10
    store = TripStore()
    owner = _owner()
    trip_id = await store.create_trip(owner)

    states = await asyncio.gather(*[store.get_state(trip_id, owner=owner) for _ in range(20)])
    assert len(states) == 20
    # 20 concurrent requests were served by at most max_size physical connections.
    assert pool.get_size() <= 10
    assert pool.get_size() < 20


@pytest.mark.asyncio
async def test_sequential_acquisitions_reuse_the_same_backend() -> None:
    await _require_db()
    pool = await get_pool()
    pids = set()
    for _ in range(10):
        async with pool.acquire() as conn:
            pids.add(await conn.fetchval("SELECT pg_backend_pid()"))
    # Releasing returns the connection to the pool; the same backend is reused.
    assert len(pids) <= pool.get_min_size()


@pytest.mark.asyncio
async def test_pool_size_is_configurable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    await _require_db()
    await close_pool()
    monkeypatch.setenv("DB_POOL_MIN", "1")
    monkeypatch.setenv("DB_POOL_MAX", "3")
    pool = await get_pool()
    assert pool.get_min_size() == 1
    assert pool.get_max_size() == 3


@pytest.mark.asyncio
async def test_close_pool_then_get_pool_creates_a_fresh_pool() -> None:
    await _require_db()
    first = await get_pool()
    await close_pool()
    second = await get_pool()
    assert first is not second


@pytest.mark.asyncio
async def test_same_trip_messages_serialize_with_monotonic_versions() -> None:
    await _require_db()
    store = TripStore()
    owner = _owner()
    trip_id = await store.create_trip(owner)
    executor = _BumpExecutor()

    traces = await asyncio.wait_for(asyncio.gather(
        store.start_run(trip_id, "first", executor, owner=owner),
        store.start_run(trip_id, "second", executor, owner=owner),
    ), timeout=10)
    assert len({trace.run_id for trace in traces}) == 2

    final = await store.get_state(trip_id, owner=owner)
    assert final.version == 2
    pool = await get_pool()
    async with pool.acquire() as conn:
        versions = [r["version"] for r in await conn.fetch(
            "SELECT version FROM trip_versions WHERE trip_id=$1 ORDER BY version", trip_id)]
        run_count = await conn.fetchval("SELECT count(*) FROM runs WHERE trip_id=$1", trip_id)
    # Both turns survived as distinct, monotonically increasing versions.
    assert versions == [0, 1, 2]
    assert run_count == 2


@pytest.mark.asyncio
async def test_different_trips_proceed_in_parallel() -> None:
    await _require_db()
    store = TripStore()
    owner = _owner()
    first = await store.create_trip(owner)
    second = await store.create_trip(owner)
    barrier = asyncio.Barrier(2)
    executor = _BarrierExecutor(barrier)

    # Both executors must reach the barrier together; serialization would deadlock.
    traces = await asyncio.wait_for(asyncio.gather(
        store.start_run(first, "a", executor, owner=owner),
        store.start_run(second, "b", executor, owner=owner),
    ), timeout=10)
    assert {trace.trip_id for trace in traces} == {first, second}
    assert (await store.get_state(first, owner=owner)).version == 1
    assert (await store.get_state(second, owner=owner)).version == 1


@pytest.mark.asyncio
async def test_no_row_lock_is_held_while_the_executor_runs() -> None:
    await _require_db()
    store = TripStore()
    owner = _owner()
    trip_id = await store.create_trip(owner)
    entered = asyncio.Event()
    release = asyncio.Event()
    executor = _SlowExecutor(entered, release)

    task = asyncio.create_task(store.start_run(trip_id, "slow", executor, owner=owner))
    await asyncio.wait_for(entered.wait(), timeout=10)
    try:
        # A concurrent reader is not blocked and still sees the pre-turn state.
        reading = await asyncio.wait_for(store.get_state(trip_id, owner=owner), timeout=10)
        assert reading.version == 0
        # The trips row itself is not locked: FOR UPDATE NOWAIT would raise
        # LockNotAvailableError if the executor ran inside the old row lock.
        pool = await get_pool()
        async with pool.acquire() as conn:
            probe = await conn.fetchval(
                "SELECT trip_id FROM trips WHERE trip_id=$1 FOR UPDATE NOWAIT", trip_id)
        assert probe == trip_id
    finally:
        release.set()
    trace = await asyncio.wait_for(task, timeout=10)
    assert trace.trip_id == trip_id
    assert (await store.get_state(trip_id, owner=owner)).version == 1


@pytest.mark.asyncio
async def test_answer_is_idempotent_and_serialized_per_trip() -> None:
    await _require_db()
    store = TripStore()
    owner = _owner()
    trip_id = await store.create_trip(owner)

    class _Interrupt:
        async def aexecute(self, state: TripState, turn: RunInput) -> RunResult:
            if turn.kind == "message":
                return RunResult(state.next_version().model_copy(update={
                    "run_status": RunStatus.INTERRUPTED, "awaiting_fields": ["origin"],
                    "outcome": Outcome.AWAITING_ANSWER,
                }), ())
            return RunResult(state.next_version().model_copy(update={
                "run_status": RunStatus.COMPLETED, "awaiting_fields": [], "outcome": Outcome.BEST_EFFORT,
            }), ())

    executor = _Interrupt()
    started = await store.start_run(trip_id, "plan", executor, owner=owner)
    assert started.status == RunStatus.INTERRUPTED
    answered = await store.answer(started.run_id, "origin", "Москва", executor, owner=owner)
    assert answered.status == RunStatus.COMPLETED
    replay = await store.answer(started.run_id, "origin", "Москва", executor, owner=owner)
    assert replay == answered
    assert (await store.get_state(trip_id, owner=owner)).version == 2
    with pytest.raises(Conflict):
        await store.answer(started.run_id, "origin", "Питер", executor, owner=owner)