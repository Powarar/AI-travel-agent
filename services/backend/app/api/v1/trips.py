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

router = APIRouter()
logger = logging.getLogger(__name__)


def _http_error(exc: NotFound | Conflict) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail="Запись не найдена")
    return HTTPException(status_code=409, detail=str(exc))


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
