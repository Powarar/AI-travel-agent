"""Concurrency, isolation, cancellation and hybrid-ranking regression checks."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from services.agent.app.llm.slots import MockSlots
from services.agent.app.orchestration.runtime import AsyncRuntime
from services.agent.app.tools.seed_sources import seed_sources
from services.backend.app.api.agent_executor import AsyncAgentRunExecutor
from services.backend.app.api.executor import RunInput
from services.backend.app.retrieval.hybrid import bm25, fuse
from shared.contracts.enums import Outcome
from shared.contracts.state import TripState
from shared.contracts.request import FieldValue
from shared.contracts.enums import ConstraintKind
from services.agent.app.orchestration import nodes
from shared.contracts.request import TripRequest

MESSAGE = "Хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, бюджет 200000"


def test_bm25_uses_term_frequency_and_length_not_raw_overlap() -> None:
    docs = {"focused": {"пляж": 3, "тих": 1}, "long": {"пляж": 1, "проч": 100}, "irrelevant": {"центр": 4}}
    scores = bm25(["пляж"], docs)
    assert scores["focused"] > scores["long"] > scores["irrelevant"] == 0


def test_rrf_rewards_agreement_without_mixing_incomparable_score_scales() -> None:
    lexical = {"exact": 1000, "both": 900}
    semantic = {"semantic": 0.9, "both": 0.8}
    scores = fuse(lexical, semantic)
    assert max(scores, key=scores.get) == "both"
    assert scores == fuse({key: value * 100 for key, value in lexical.items()}, semantic)


def test_followup_does_not_reset_explicit_hotel_requirements_to_defaults() -> None:
    base = TripRequest()
    base.lodging.must = FieldValue[list[str]].known(["pool"], kind=ConstraintKind.HARD, source="user")
    base.party.adults = FieldValue[int].known(4, kind=ConstraintKind.HARD, source="user")
    delta = TripRequest()
    delta.lodging.must = FieldValue[list[str]].defaulted([], kind=ConstraintKind.HARD)
    delta.party.adults = FieldValue[int].defaulted(2, kind=ConstraintKind.HARD)
    merged, conflicts = nodes.merge_request(base, delta)
    assert merged.lodging.must.value == ["pool"]
    assert merged.party.adults.value == 4
    assert conflicts == []


@pytest.mark.asyncio
async def test_independent_dialogues_overlap_without_a_global_lock() -> None:
    arrived = 0
    ready = asyncio.Event()

    class Slots(MockSlots):
        async def parse(self, message):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                ready.set()
            await ready.wait()
            return super().parse(message)

    engine = AsyncAgentRunExecutor(seed_sources, Slots)
    states = [TripState(trip_id=uuid4()) for _ in range(2)]
    results = await asyncio.wait_for(asyncio.gather(*[
        engine.aexecute(state, RunInput(uuid4(), state.trip_id, "message", text=MESSAGE)) for state in states
    ]), timeout=3)
    assert all(result.state.outcome == Outcome.PLAN_READY for result in results)
    assert {result.state.trip_id for result in results} == {state.trip_id for state in states}


@pytest.mark.asyncio
async def test_selective_flight_replan_survives_a_new_executor_instance() -> None:
    first_engine = AsyncAgentRunExecutor(seed_sources, MockSlots)
    state = TripState(trip_id=uuid4())
    first = await first_engine.aexecute(state, RunInput(uuid4(), state.trip_id, "message", text=MESSAGE))
    calls = []

    def sources():
        def wrap(name, source):
            async def call(args):
                calls.append(name)
                return source(args)
            return call
        return {name: wrap(name, source) for name, source in seed_sources().items()}

    restarted = AsyncAgentRunExecutor(sources, MockSlots)
    second = await restarted.aexecute(first.state, RunInput(uuid4(), state.trip_id, "message",
                                        text="Только прямые рейсы", resume=first.resume))
    assert second.state.outcome == Outcome.PLAN_READY
    assert "transport_search" in calls
    assert "hotel_search" not in calls
    assert "suggest_destinations" not in calls
    assert first.state.chosen.component_refs["hotel"] == second.state.chosen.component_refs["hotel"]


@pytest.mark.asyncio
async def test_cancellation_closes_provider_without_turning_into_a_fallback() -> None:
    entered = asyncio.Event()
    closed = False

    class Slots(MockSlots):
        async def parse(self, message):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            nonlocal closed
            closed = True

    state = TripState(trip_id=uuid4())
    task = asyncio.create_task(AsyncAgentRunExecutor(seed_sources, Slots).aexecute(
        state, RunInput(uuid4(), state.trip_id, "message", text=MESSAGE)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed
