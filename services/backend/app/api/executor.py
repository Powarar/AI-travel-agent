"""Single boundary between HTTP and the agent runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from shared.contracts.state import TripState

from .schemas import SSEEvent


@dataclass(frozen=True)
class RunInput:
    run_id: UUID
    trip_id: UUID
    kind: Literal["message", "answer"]
    text: str | None = None
    field: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class RunResult:
    state: TripState
    events: tuple[SSEEvent, ...]


class RunExecutor(Protocol):
    def execute(self, state: TripState, turn: RunInput) -> RunResult: ...


class FakeRunExecutor:
    """Returns scripted results without calling the orchestrator or a provider."""

    def __init__(self, results: list[RunResult]) -> None:
        self._results = deque(results)
        self.calls: list[RunInput] = []

    def execute(self, state: TripState, turn: RunInput) -> RunResult:
        if not self._results:
            raise RuntimeError("FakeRunExecutor has no scripted result")
        self.calls.append(turn)
        result = self._results.popleft()
        if result.state.trip_id != turn.trip_id:
            raise ValueError("scripted state belongs to another trip")
        return result


class FixtureRunExecutor:
    """Minimal offline fixture for a running API until the agent is connected."""

    def execute(self, state: TripState, turn: RunInput) -> RunResult:
        from shared.contracts.enums import Outcome, RunStatus

        if turn.kind == "message":
            next_state = state.next_version().model_copy(update={
                "run_status": RunStatus.INTERRUPTED,
                "awaiting_fields": ["origin"],
                "outcome": Outcome.AWAITING_ANSWER,
            })
            events = (SSEEvent(event="question", data={
                "field": "origin", "text": "Откуда вы отправляетесь?",
                "options": [], "allow_skip": False,
            }),)
        else:
            next_state = state.next_version().model_copy(update={
                "run_status": RunStatus.COMPLETED,
                "awaiting_fields": [],
                "outcome": Outcome.BEST_EFFORT,
            })
            events = (SSEEvent(event="answer_text", data={
                "block_id": "fixture", "text": "Ответ сохранён.",
                "numbers_verified": True,
            }),)
        return RunResult(state=next_state, events=events)
