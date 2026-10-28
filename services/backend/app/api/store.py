"""Postgres persistence for trip state and resumable HTTP runs."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]

from shared.contracts.enums import RunStatus
from shared.contracts.state import TripState

from .executor import InvalidAnswer, RunExecutor, RunInput, RunResult
from .schemas import CheckoutCompleted, RunTrace, SSEEvent


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


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
    """Ход агента синхронный и с живым LLM длится секунды: в поток, чтобы не стоял event loop."""
    try:
        return await asyncio.to_thread(executor.execute, state, turn)
    except InvalidAnswer as exc:
        raise Conflict(str(exc)) from exc


class TripStore:
    def __init__(self, dsn: str | None = None) -> None:
        raw = dsn or os.environ.get("DATABASE_URL", "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok")
        self.dsn = raw.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn)

    async def create_trip(self) -> UUID:
        trip_id = uuid4()
        state = TripState(trip_id=trip_id)
        conn = await self._connect()
        try:
            async with conn.transaction():
                payload = state.model_dump_json()
                await conn.execute("INSERT INTO trips VALUES ($1, $2::jsonb, 0)", trip_id, payload)
                await conn.execute("INSERT INTO trip_versions VALUES ($1, 0, $2::jsonb)", trip_id, payload)
        finally:
            await conn.close()
        return trip_id

    async def get_state(self, trip_id: UUID, version: int | None = None) -> TripState:
        conn = await self._connect()
        try:
            if version is None:
                row = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1", trip_id)
            else:
                row = await conn.fetchrow(
                    "SELECT state FROM trip_versions WHERE trip_id=$1 AND version=$2", trip_id, version,
                )
            if row is None:
                raise NotFound
            return _state(row["state"])
        finally:
            await conn.close()

    async def get_run(self, run_id: UUID) -> RunTrace:
        conn = await self._connect()
        try:
            row = await conn.fetchrow("SELECT * FROM runs WHERE run_id=$1", run_id)
            if row is None:
                raise NotFound
            return _trace(row)
        finally:
            await conn.close()

    async def start_run(self, trip_id: UUID, message: str, executor: RunExecutor) -> RunTrace:
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1 FOR UPDATE", trip_id)
                if row is None:
                    raise NotFound
                state = _state(row["state"])
                if state.run_status == RunStatus.INTERRUPTED:
                    raise Conflict("Сначала ответьте на текущий вопрос")
                run_id = uuid4()
                result = await _execute(executor, state, RunInput(run_id, trip_id, "message", text=message))
                self._check_result(state, result.state)
                events = [SSEEvent(event="run_started", data={"run_id": str(run_id), "trip_id": str(trip_id)}), *result.events]
                await self._save_state(conn, result.state)
                await conn.execute(
                    "INSERT INTO runs(run_id,trip_id,status,awaiting,events,message,agent_run) "
                    "VALUES($1,$2,$3,$4,$5::jsonb,$6,$7)",
                    run_id, trip_id, result.state.run_status.value, result.state.awaiting_fields,
                    json.dumps([e.model_dump(mode="json") for e in events], ensure_ascii=False), message,
                    result.resume,
                )
                return RunTrace(run_id=run_id, trip_id=trip_id, status=result.state.run_status,
                                awaiting=result.state.awaiting_fields, events=events)
        finally:
            await conn.close()

    async def answer(self, run_id: UUID, field: str, value: str, executor: RunExecutor) -> RunTrace:
        conn = await self._connect()
        try:
            async with conn.transaction():
                run = await conn.fetchrow("SELECT * FROM runs WHERE run_id=$1 FOR UPDATE", run_id)
                if run is None:
                    raise NotFound
                previous = await conn.fetchrow(
                    "SELECT value FROM run_answers WHERE run_id=$1 AND field=$2", run_id, field,
                )
                if previous is not None:
                    if previous["value"] != value:
                        raise Conflict("Ответ на это поле уже сохранён с другим значением")
                    return _trace(run)
                if run["status"] != RunStatus.INTERRUPTED.value or field not in run["awaiting"]:
                    raise Conflict("Этот ход не ожидает ответ на указанное поле")
                trip = await conn.fetchrow("SELECT state FROM trips WHERE trip_id=$1 FOR UPDATE", run["trip_id"])
                if trip is None:
                    raise NotFound
                state = _state(trip["state"])
                result = await _execute(executor, state, RunInput(
                    run_id, run["trip_id"], "answer", field=field, value=value, resume=run["agent_run"],
                ))
                self._check_result(state, result.state)
                prior_events = _trace(run).events
                events = [*prior_events, *result.events]
                await self._save_state(conn, result.state)
                await conn.execute("INSERT INTO run_answers VALUES($1,$2,$3)", run_id, field, value)
                await conn.execute(
                    "UPDATE runs SET status=$2, awaiting=$3, events=$4::jsonb, agent_run=$5 WHERE run_id=$1",
                    run_id, result.state.run_status.value, result.state.awaiting_fields,
                    json.dumps([e.model_dump(mode="json") for e in events], ensure_ascii=False),
                    result.resume,
                )
                return RunTrace(run_id=run_id, trip_id=run["trip_id"], status=result.state.run_status,
                                awaiting=result.state.awaiting_fields, events=events)
        finally:
            await conn.close()

    async def checkout(self, trip_id: UUID, key: str) -> CheckoutCompleted:
        conn = await self._connect()
        try:
            async with conn.transaction():
                exists = await conn.fetchval("SELECT 1 FROM trips WHERE trip_id=$1", trip_id)
                if exists is None:
                    raise NotFound
                order_id = str(uuid4())
                inserted = await conn.fetchval(
                    "INSERT INTO checkout_orders VALUES($1,$2,$3) "
                    "ON CONFLICT DO NOTHING RETURNING order_id", trip_id, key, order_id,
                )
                if inserted is None:
                    order_id = await conn.fetchval(
                        "SELECT order_id FROM checkout_orders WHERE trip_id=$1 AND idempotency_key=$2",
                        trip_id, key,
                    )
                return CheckoutCompleted(order_id=order_id, idempotent_replay=inserted is None)
        finally:
            await conn.close()

    @staticmethod
    def _check_result(before: TripState, after: TripState) -> None:
        if after.trip_id != before.trip_id or after.version != before.version + 1:
            raise ValueError("executor returned invalid trip id or version")

    @staticmethod
    async def _save_state(conn: asyncpg.Connection, state: TripState) -> None:
        payload = state.model_dump_json()
        await conn.execute("UPDATE trips SET state=$2::jsonb, version=$3 WHERE trip_id=$1",
                           state.trip_id, payload, state.version)
        await conn.execute("INSERT INTO trip_versions VALUES($1,$2,$3::jsonb)",
                           state.trip_id, state.version, payload)
