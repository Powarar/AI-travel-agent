"""Postgres persistence for trip state and resumable HTTP runs.

Concurrency
-----------
A turn is two short transactions with the executor (network/LLM I/O) between
them, so no row lock and no pooled connection is held while the model runs:

1. read the trip state and its ``version`` (and ownership), then release the
   connection;
2. run the executor against that snapshot;
3. write the result with a compare-and-swap ``UPDATE ... WHERE version = $read``.

Same-trip serialization is optimistic: if another turn advanced the version
between (1) and (3) the CAS matches zero rows, the result is discarded and the
whole turn is retried against the fresh state. Two concurrent messages on one
trip therefore apply sequentially (v0 -> v1 -> v2) with no lost update, while two
different trips never contend. This preserves the guarantees the old
``SELECT ... FOR UPDATE``-across-the-LLM code gave (serialization, the
INTERRUPTED conflict, idempotent answers, ``_check_result`` monotonicity and
resumability) without blocking readers or holding a connection for up to 45s.

The trade-off is that a losing turn re-runs its executor; contention is rare and
the retry budget is bounded (``_MAX_WRITE_RETRIES``), after which the caller gets
a 409 instead of an unbounded spin. Releasing the connection during the executor
also avoids nesting pool acquisitions: the agent's catalogue queries draw from
the same pool, so holding a trip connection across the LLM could deadlock at
``max_size`` concurrent trips.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]

from shared.contracts.enums import RunStatus
from shared.contracts.state import TripState

from .executor import InvalidAnswer, RunExecutor, RunInput, RunResult
from .orders import build_snapshot
from .schemas import CheckoutCompleted, OrderOut, OrderSnapshot, RunTrace, SSEEvent

_DEFAULT_DSN = "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok"
_MAX_WRITE_RETRIES = 5

_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


async def get_pool() -> asyncpg.Pool:
    """Lazily create one shared pool per running event loop.

    The pool is bound to the loop that created it; tests run each case in a fresh
    loop, so a stale pool from a closed loop is terminated before a new one is
    created. ``close_pool`` is the graceful shutdown path used by the app lifespan.
    """
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is loop and not loop.is_closed():
        return _pool
    if _pool is not None:
        # The previous loop is already closed; its connections die with it.
        with contextlib.suppress(RuntimeError):
            _pool.terminate()
        _pool = None
        _pool_loop = None
    _pool = await asyncpg.create_pool(
        _dsn(),
        min_size=int(os.environ.get("DB_POOL_MIN", "2")),
        max_size=int(os.environ.get("DB_POOL_MAX", "10")),
    )
    _pool_loop = loop
    return _pool


async def close_pool() -> None:
    global _pool, _pool_loop
    pool, _pool, _pool_loop = _pool, None, None
    if pool is not None:
        await pool.close()


def _json(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _state(value: object) -> TripState:
    return TripState.model_validate_json(_json(value))


def _trace(row: asyncpg.Record) -> RunTrace:
    events = json.loads(row["events"]) if isinstance(row["events"], str) else row["events"]
    return RunTrace(
        run_id=row["run_id"], trip_id=row["trip_id"], status=row["status"],
        awaiting=list(row["awaiting"]), events=[SSEEvent.model_validate(e) for e in events],
    )


async def _execute(executor: RunExecutor, state: TripState, turn: RunInput) -> RunResult:
    """The HTTP executor awaits network I/O directly; fixtures do no network I/O."""
    try:
        async_execute = getattr(executor, "aexecute", None)
        if async_execute is not None:
            result: RunResult = await async_execute(state, turn)
            return result
        return executor.execute(state, turn)
    except InvalidAnswer as exc:
        raise Conflict(str(exc)) from exc


class TripStore:
    def __init__(self, dsn: str | None = None) -> None:
        raw = dsn or os.environ.get("DATABASE_URL", _DEFAULT_DSN)
        #: informational DSN (e.g. for test reachability probes); the shared pool
        #: is configured from DATABASE_URL / DB_POOL_* by ``get_pool``.
        self.dsn = raw.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def create_trip(self, owner: str) -> UUID:
        trip_id = uuid4()
        state = TripState(trip_id=trip_id)
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            payload = state.model_dump_json()
            await conn.execute(
                "INSERT INTO trips(trip_id, state, version, owner) VALUES($1, $2::jsonb, 0, $3)",
                trip_id, payload, owner,
            )
            await conn.execute(
                "INSERT INTO trip_versions(trip_id, version, state) VALUES($1, 0, $2::jsonb)",
                trip_id, payload,
            )
        return trip_id

    async def get_state(self, trip_id: UUID, version: int | None = None, *, owner: str) -> TripState:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if version is None:
                row = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1 AND owner=$2", trip_id, owner)
            else:
                owned = await conn.fetchval("SELECT 1 FROM trips WHERE trip_id=$1 AND owner=$2", trip_id, owner)
                if owned is None:
                    raise NotFound
                row = await conn.fetchrow(
                    "SELECT state FROM trip_versions WHERE trip_id=$1 AND version=$2", trip_id, version,
                )
            if row is None:
                raise NotFound
            return _state(row["state"])

    async def get_run(self, run_id: UUID, *, owner: str) -> RunTrace:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT r.* FROM runs r JOIN trips t ON t.trip_id=r.trip_id "
                "WHERE r.run_id=$1 AND t.owner=$2", run_id, owner,
            )
            if row is None:
                raise NotFound
            return _trace(row)

    async def start_run(self, trip_id: UUID, message: str, executor: RunExecutor, *, owner: str) -> RunTrace:
        for _ in range(_MAX_WRITE_RETRIES):
            pool = await get_pool()
            async with pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow("SELECT state, owner FROM trips WHERE trip_id=$1", trip_id)
                if row is None or row["owner"] != owner:
                    raise NotFound
                state = _state(row["state"])
                if state.run_status == RunStatus.INTERRUPTED:
                    raise Conflict("Сначала ответьте на текущий вопрос")
                expected_version = state.version
                run_id = uuid4()
                resume = await conn.fetchval(
                    "SELECT r.agent_run FROM runs r WHERE r.trip_id=$1 "
                    "AND (r.agent_run::jsonb -> 'state' ->> 'version')::int = $2 LIMIT 1",
                    trip_id, expected_version,
                )
            # No transaction and no pooled connection is held while the executor runs.
            result = await _execute(executor, state, RunInput(run_id, trip_id, "message", text=message, resume=resume))
            self._check_result(state, result.state)
            events = [SSEEvent(event="run_started", data={"run_id": str(run_id), "trip_id": str(trip_id)}),
                      *result.events]
            async with pool.acquire() as conn, conn.transaction():
                if not await self._cas_state(conn, trip_id, expected_version, result.state):
                    continue
                await conn.execute(
                    "INSERT INTO runs(run_id,trip_id,status,awaiting,events,message,agent_run) "
                    "VALUES($1,$2,$3,$4,$5::jsonb,$6,$7)",
                    run_id, trip_id, result.state.run_status.value, result.state.awaiting_fields,
                    json.dumps([e.model_dump(mode="json") for e in events], ensure_ascii=False), message,
                    result.resume,
                )
            return RunTrace(run_id=run_id, trip_id=trip_id, status=result.state.run_status,
                            awaiting=result.state.awaiting_fields, events=events)
        raise Conflict("Слишком много одновременных изменений этого плана, попробуйте ещё раз")

    async def answer(self, run_id: UUID, field: str, value: str, executor: RunExecutor, *, owner: str) -> RunTrace:
        for _ in range(_MAX_WRITE_RETRIES):
            pool = await get_pool()
            async with pool.acquire() as conn, conn.transaction():
                run = await conn.fetchrow(
                    "SELECT r.* FROM runs r JOIN trips t ON t.trip_id=r.trip_id "
                    "WHERE r.run_id=$1 AND t.owner=$2", run_id, owner,
                )
                if run is None:
                    raise NotFound
                trip_id = run["trip_id"]
                previous = await conn.fetchrow(
                    "SELECT value FROM run_answers WHERE run_id=$1 AND field=$2", run_id, field,
                )
                if previous is not None:
                    if previous["value"] != value:
                        raise Conflict("Ответ на это поле уже сохранён с другим значением")
                    return _trace(run)
                if run["status"] != RunStatus.INTERRUPTED.value or field not in run["awaiting"]:
                    raise Conflict("Этот ход не ожидает ответ на указанное поле")
                trip = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1", trip_id)
                if trip is None:
                    raise NotFound
                state = _state(trip["state"])
                expected_version = state.version
            result = await _execute(executor, state, RunInput(
                run_id, trip_id, "answer", field=field, value=value, resume=run["agent_run"],
            ))
            self._check_result(state, result.state)
            events = [*_trace(run).events, *result.events]
            async with pool.acquire() as conn, conn.transaction():
                if not await self._cas_state(conn, trip_id, expected_version, result.state):
                    continue
                inserted = await conn.fetchval(
                    "INSERT INTO run_answers(run_id,field,value) VALUES($1,$2,$3) "
                    "ON CONFLICT (run_id,field) DO NOTHING RETURNING field", run_id, field, value,
                )
                if inserted is None:
                    raise Conflict("Ответ на это поле уже сохранён с другим значением")
                await conn.execute(
                    "UPDATE runs SET status=$2, awaiting=$3, events=$4::jsonb, agent_run=$5 WHERE run_id=$1",
                    run_id, result.state.run_status.value, result.state.awaiting_fields,
                    json.dumps([e.model_dump(mode="json") for e in events], ensure_ascii=False),
                    result.resume,
                )
            return RunTrace(run_id=run_id, trip_id=trip_id, status=result.state.run_status,
                            awaiting=result.state.awaiting_fields, events=events)
        raise Conflict("Слишком много одновременных изменений этого плана, попробуйте ещё раз")

    async def checkout(self, trip_id: UUID, key: str, owner: str) -> CheckoutCompleted:
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1 AND owner=$2", trip_id, owner)
            if row is None:
                raise NotFound
            state = _state(row["state"])
            order_id = str(uuid4())
            # Снимок плана на момент оформления: заказ не меняется вслед за поздними правками trip.
            inserted = await conn.fetchval(
                "INSERT INTO checkout_orders(trip_id,idempotency_key,order_id,owner,state_version,snapshot) "
                "VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT DO NOTHING RETURNING order_id",
                trip_id, key, order_id, owner, state.version, build_snapshot(state).model_dump_json(),
            )
            if inserted is None:
                order_id = await conn.fetchval(
                    "SELECT order_id FROM checkout_orders WHERE trip_id=$1 AND idempotency_key=$2",
                    trip_id, key,
                )
            return CheckoutCompleted(order_id=order_id, idempotent_replay=inserted is None)

    async def list_orders(self, owner: str) -> list[OrderOut]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT order_id, trip_id, created_at, snapshot FROM checkout_orders "
                "WHERE owner=$1 ORDER BY created_at DESC", owner,
            )
        return [OrderOut(
            order_id=r["order_id"], trip_id=r["trip_id"], created_at=r["created_at"],
            snapshot=OrderSnapshot.model_validate_json(_json(r["snapshot"])) if r["snapshot"] else None,
        ) for r in rows]

    @staticmethod
    def _check_result(before: TripState, after: TripState) -> None:
        if after.trip_id != before.trip_id or after.version != before.version + 1:
            raise ValueError("executor returned invalid trip id or version")

    @staticmethod
    async def _cas_state(conn: asyncpg.Connection, trip_id: UUID, expected_version: int, state: TripState) -> bool:
        """Compare-and-swap the trip row; False means another turn advanced it first."""
        payload = state.model_dump_json()
        claimed = await conn.fetchval(
            "UPDATE trips SET state=$2::jsonb, version=$3 WHERE trip_id=$1 AND version=$4 RETURNING trip_id",
            trip_id, payload, state.version, expected_version,
        )
        if claimed is None:
            return False
        await conn.execute("INSERT INTO trip_versions(trip_id,version,state) VALUES($1,$2,$3::jsonb)",
                           trip_id, state.version, payload)
        return True