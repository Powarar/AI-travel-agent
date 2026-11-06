"""Trip API backed by persisted state and one executor boundary."""

from __future__ import annotations

import logging
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from shared.contracts.state import TripState

from ..dependencies import get_executor, get_store
from ..executor import RunExecutor
from ..identity import get_owner, require_owner
from ..schemas import AnswerIn, CheckoutCompleted, MessageIn, OrderOut, RunTrace, TripCreated
from ..store import Conflict, NotFound, TripStore
from ...metrics.schemas import MonitoringSummary
from ...metrics.service import collector
import asyncpg  # type: ignore[import-untyped]
from ...retrieval.catalogue import database_url
from ...metrics.models import summarize_costs
from shared.evaluation_metrics import parse_accuracy_summary

router = APIRouter()
logger = logging.getLogger(__name__)


def _http_error(exc: NotFound | Conflict) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail="Запись не найдена")
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/monitoring/summary", response_model=MonitoringSummary)
async def monitoring_summary() -> MonitoringSummary:
    """Aggregated operational and quality metrics; never exposes prompts or secrets."""
    return collector.summary()


@router.get("/monitoring/models")
async def model_metrics() -> dict[str, object]:
    """Persistent model/slot aggregates derived from recorded HTTP turns."""
    conn = await asyncpg.connect(database_url())
    try:
        rows = await conn.fetch("""
            WITH spans AS (
                SELECT event->'data' AS data FROM runs,
                LATERAL jsonb_array_elements(events) AS event
                WHERE event->>'event' = 'llm_call' AND event->'data'->>'model' IS NOT NULL
            )
            SELECT data->>'slot' AS slot, data->>'model' AS model,
                count(*) AS calls,
                count(*) FILTER (WHERE data->>'status' <> 'ok') AS errors,
                count(*) FILTER (WHERE data->>'fallback' = 'true') AS fallbacks,
                sum((data->>'input_tokens')::bigint)::bigint AS input_tokens,
                sum((data->>'output_tokens')::bigint)::bigint AS output_tokens,
                sum((data->>'total_tokens')::bigint)::bigint AS total_tokens,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY (data->>'latency_ms')::int) AS p50_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY (data->>'latency_ms')::int) AS p95_ms
            FROM spans GROUP BY data->>'slot', data->>'model' ORDER BY 1, 2
        """)
        costs = await conn.fetch("""
            SELECT event->'data' AS data FROM runs,
            LATERAL jsonb_array_elements(events) AS event
            WHERE event->>'event'='llm_call' AND event->'data'->>'model' IS NOT NULL
        """)
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for cost in costs:
            span = cost["data"] if isinstance(cost["data"], dict) else json.loads(cost["data"])
            grouped.setdefault((span["slot"], span["model"]), []).append(span)
        quality_rows = await conn.fetch("""
            SELECT DISTINCT ON(model, dataset_id, dataset_hash, prompt_version) model, report
            FROM evaluation_runs WHERE mode='live'
            ORDER BY model, dataset_id, dataset_hash, prompt_version, created_at DESC
        """)
        quality: dict[str, list[dict[str, object]]] = {}
        for item in quality_rows:
            report = item["report"] if isinstance(item["report"], dict) else json.loads(item["report"])
            if report.get("rows"):
                report = {**report, **parse_accuracy_summary(report["rows"])}
            quality.setdefault(item["model"], []).append({key: value for key, value in report.items()
                                                         if key not in {"rows", "spans"}})
        return {"window": "persisted HTTP runs", "models": [
            {**dict(row), **summarize_costs(grouped.get((row["slot"], row["model"]), [])),
             "accuracy_evaluations": quality.get(row["model"], []) if row["slot"] == "parse" else []} for row in rows
        ]}
    finally:
        await conn.close()


@router.get("/monitoring/accuracy")
async def accuracy_metrics() -> dict[str, object]:
    conn = await asyncpg.connect(database_url())
    try:
        rows = await conn.fetch("""
            SELECT DISTINCT ON(dataset_id, dataset_hash, model, prompt_version, mode)
                eval_id, created_at, dataset_id, dataset_hash, model, prompt_version, mode, report
            FROM evaluation_runs
            ORDER BY dataset_id, dataset_hash, model, prompt_version, mode, created_at DESC
        """)
        evaluations = []
        for row in rows:
            report = row["report"] if isinstance(row["report"], dict) else json.loads(row["report"])
            if report.get("rows"):
                report = {**report, **parse_accuracy_summary(report["rows"])}
            evaluations.append({**{key: row[key] for key in ("eval_id", "created_at", "dataset_id", "dataset_hash",
                                                           "model", "prompt_version", "mode")},
                                **{key: value for key, value in report.items() if key not in {"rows", "spans"}}})
        return {"source": "labelled evaluation datasets", "evaluations": evaluations,
                "note": "Production calls have no ground truth; accuracy is measured only on labelled evaluations."}
    finally:
        await conn.close()


@router.post("/trips", response_model=TripCreated, status_code=201)
async def create_trip(store: TripStore = Depends(get_store)) -> TripCreated:
    trip_id = await store.create_trip()
    logger.info("Trip created: %s", trip_id)
    return TripCreated(trip_id=trip_id)


@router.get("/trips/{trip_id}/state", response_model=TripState)
async def get_state(trip_id: UUID, version: int | None = Query(default=None, ge=0),
                    store: TripStore = Depends(get_store)) -> TripState:
    try:
        return await store.get_state(trip_id, version)
    except NotFound as exc:
        raise _http_error(exc) from exc


def _stream(trace: RunTrace) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        for event in trace.events:
            yield f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/trips/{trip_id}/messages", response_model=None)
async def post_message(trip_id: UUID, body: MessageIn,
                       store: TripStore = Depends(get_store),
                       executor: RunExecutor = Depends(get_executor)) -> StreamingResponse:
    try:
        trace = await store.start_run(trip_id, body.text, executor)
    except (NotFound, Conflict) as exc:
        raise _http_error(exc) from exc
    logger.info("Run started: %s", trace.run_id)
    return _stream(trace)


@router.get("/runs/{run_id}", response_model=RunTrace)
async def get_run(run_id: UUID, store: TripStore = Depends(get_store)) -> RunTrace:
    try:
        return await store.get_run(run_id)
    except NotFound as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/answers", response_model=RunTrace)
async def post_answer(run_id: UUID, body: AnswerIn,
                      store: TripStore = Depends(get_store),
                      executor: RunExecutor = Depends(get_executor)) -> RunTrace:
    try:
        trace = await store.answer(run_id, body.field, body.value, executor)
    except (NotFound, Conflict) as exc:
        raise _http_error(exc) from exc
    logger.info("Run answered: %s", run_id)
    return trace


@router.post("/trips/{trip_id}/checkout", response_model=CheckoutCompleted)
async def checkout(trip_id: UUID, idempotency_key: str = Header(min_length=1),
                   owner: str | None = Depends(get_owner),
                   store: TripStore = Depends(get_store)) -> CheckoutCompleted:
    try:
        result = await store.checkout(trip_id, idempotency_key, owner)
    except NotFound as exc:
        raise _http_error(exc) from exc
    logger.info("Checkout requested: %s", trip_id)
    return result


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(owner: str = Depends(require_owner),
                      store: TripStore = Depends(get_store)) -> list[OrderOut]:
    """Заказы вызывающего: Telegram-пользователя по подписанному initData или браузера по X-Client-Id."""
    return await store.list_orders(owner)
