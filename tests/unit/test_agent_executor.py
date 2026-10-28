"""Агент за границей RunExecutor: события SSE-контракта и возобновление после рестарта."""

from __future__ import annotations

from uuid import uuid4

import pytest

from services.agent.app.llm.slots import MockSlots
from services.agent.app.tools.seed_sources import seed_sources
from services.backend.app.api.agent_executor import AgentRunExecutor
from services.backend.app.api.executor import InvalidAnswer, RunInput, RunResult
from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState

GOLDEN = "хочу к тёплому морю в ноябре вдвоём с ребёнком, 200к, без долгих перелётов"


def _executor() -> AgentRunExecutor:
    # Новый экземпляр = новый процесс: памяти прошлого хода у него нет.
    return AgentRunExecutor(seed_sources(), slots=MockSlots())


def _names(result: RunResult) -> list[str]:
    return [e.event for e in result.events]


def _data(result: RunResult, name: str) -> list[dict[str, object]]:
    return [e.data for e in result.events if e.event == name]


def _answer(state: TripState, previous: RunResult, field: str, value: str) -> RunResult:
    return _executor().execute(state, RunInput(uuid4(), state.trip_id, "answer", field=field, value=value,
                                               resume=previous.resume))


def test_golden_path_resumes_from_serialized_run_in_a_new_process() -> None:
    start = TripState(trip_id=uuid4())
    first = _executor().execute(start, RunInput(uuid4(), start.trip_id, "message", text=GOLDEN))
    assert first.state.version == 1 and first.state.run_status == RunStatus.INTERRUPTED
    assert first.resume is not None
    [question] = _data(first, "question")
    assert question["field"] == "origin" and question["options"]

    second = _answer(first.state, first, "origin", "Москва")
    assert second.state.version == 2
    assert [q["field"] for q in _data(second, "question")] == ["dates.start"]

    done = _answer(second.state, second, "dates.start", "с 14 по 21")
    assert done.state.version == 3 and done.resume is None
    assert done.state.outcome == Outcome.PLAN_READY
    names = _names(done)
    assert "question" not in names and "phase_changed" in names
    tools = _data(done, "tool_completed")
    assert {t["tool"] for t in tools} >= {"transport_search", "hotel_search", "transfer_search"}
    assert all(isinstance(t["ms"], int) and t["status"] == "ok" for t in tools)
    [destinations] = [c for c in _data(done, "candidates") if c["kind"] == "destination"]
    assert len(destinations["items"]) == 3  # type: ignore[arg-type]
    [plan] = _data(done, "plan_updated")
    assert plan["version"] == 3 and plan["kept_components"] == []
    assert "destination" in plan["changed_components"]  # type: ignore[operator]
    [text] = _data(done, "answer_text")
    assert text["numbers_verified"] is True and text["text"]


def test_unreadable_answer_is_refused_not_crashed() -> None:
    start = TripState(trip_id=uuid4())
    first = _executor().execute(start, RunInput(uuid4(), start.trip_id, "message", text=GOLDEN))
    second = _answer(first.state, first, "origin", "Москва")
    with pytest.raises(InvalidAnswer):
        _answer(second.state, second, "dates.start", "когда-нибудь потом")


def test_change_in_same_trip_reports_changed_and_kept_components() -> None:
    executor = _executor()
    start = TripState(trip_id=uuid4())
    message = "хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, бюджет 200000"
    first = executor.execute(start, RunInput(uuid4(), start.trip_id, "message", text=message))
    assert first.state.outcome == Outcome.PLAN_READY
    second = executor.execute(first.state, RunInput(uuid4(), start.trip_id, "message", text="у нас собака"))
    assert second.state.version == first.state.version + 1
    assert second.state.outcome == Outcome.PLAN_READY
    [plan] = _data(second, "plan_updated")
    # собака меняет отель и его трансферы, но не направление и не рейсы
    assert {"hotel", "transfer_arrival", "transfer_departure"} <= set(plan["changed_components"])  # type: ignore[arg-type]
    assert {"destination", "transport_in", "transport_out"} <= set(plan["kept_components"])  # type: ignore[arg-type]
