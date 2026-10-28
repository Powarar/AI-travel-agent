"""Агент за границей `RunExecutor`: оркестратор T-05 + события SSE-контракта.

Ход исполняет `Runtime` агента; события строятся из того, что реально произошло за ход:
фазы — из `run.trace`, инструменты — из обёртки над источниками (время и число строк
измерены здесь, а не придуманы), вопросы, кандидаты, нарушения и план — из `TripState`.
Прерванный ход уходит в `runs.agent_run` (`Runtime.dump`) и возобновляется оттуда же,
поэтому ответ переживает рестарт API.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from services.agent.app.orchestration.runtime import Run, Runtime, Source
from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState

from .executor import InvalidAnswer, RunInput, RunResult
from .schemas import SSEEvent

logger = logging.getLogger(__name__)

#: срезы `SearchResult`, по которым считается «сколько в выдаче» у каждого инструмента
_TOOL_SLICES: dict[str, tuple[str, ...]] = {
    "suggest_destinations": ("destinations",),
    "weather_climate": ("climate",),
    "transport_search": ("transport",),
    "hotel_search": ("hotels",),
    "transfer_search": ("transfers",),
    "checkout_mock": (),
}


@dataclass(frozen=True)
class ToolCall:
    tool: str
    ms: int
    count: int
    status: str


class _Recorder:
    """Обёртка источников: каждый настоящий вызов оставляет след для `tool_completed`."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def wrap(self, name: str, source: Source) -> Source:
        def call(args: Mapping[str, object]) -> Any:
            started = time.perf_counter()
            try:
                result = source(args)
            except Exception:
                self.calls.append(ToolCall(name, _ms(started), 0, "error"))
                raise
            count = sum(len(getattr(result, part, ())) for part in _TOOL_SLICES.get(name, ()))
            self.calls.append(ToolCall(name, _ms(started), count, "ok"))
            return result

        return call


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _event(name: str, **data: object) -> SSEEvent:
    return SSEEvent.model_validate({"event": name, "data": data})


def _refs(state: TripState) -> dict[str, str]:
    return {k: v for k, v in state.chosen.component_refs.items() if k != "order"}


def build_events(before: TripState, run: Run, trace_from: int, calls: list[ToolCall]) -> list[SSEEvent]:
    state = run.state
    events: list[SSEEvent] = []
    phase = before.stage.value
    for stage in run.trace[trace_from:]:
        if stage != phase:
            events.append(_event("phase_changed", **{"from": phase, "to": stage}))
            phase = stage
    for call in calls:
        events.append(_event("tool_started", tool=call.tool, args_hash="", from_cache=False))
        events.append(_event("tool_completed", tool=call.tool, ms=call.ms, count=call.count, status=call.status))

    finalists = [c for c in state.candidates.destinations if c.ref.get("finalist") == "true"]
    if finalists and finalists != [c for c in before.candidates.destinations if c.ref.get("finalist") == "true"]:
        events.append(_event("candidates", kind="destination", items=[c.model_dump(mode="json") for c in finalists],
                             ranking_factors=["day_temp_c", "flight_h"]))
    if run.alternatives:
        events.append(_event("candidates", kind="alternative_destination",
                             items=[item.model_dump(mode="json") for item in run.alternatives],
                             ranking_factors=["season", "theme", "flight_h"]))
    chosen = _refs(state).get("destination")
    if chosen and state.candidates.hotels != before.candidates.hotels:
        hotels = [c for c in state.candidates.hotels if c.ref.get("destination") == chosen]
        hotels.sort(key=lambda c: (-float(c.ref.get("relevance") or 0), -(c.score or 0)))
        events.append(_event("candidates", kind="hotel", items=[c.model_dump(mode="json") for c in hotels[:5]],
                             ranking_factors=["relevance", "price_per_night"]))

    for v in state.violations:
        events.append(_event("violation", code=v.code.value, severity=v.severity.value, expected=v.expected,
                             actual=v.actual, affected_component=v.affected_component))

    new, old = _refs(state), _refs(before)
    if new and new != old:
        events.append(_event("plan_updated", version=before.version + 1,
                             changed_components=sorted(k for k in new if old.get(k) != new[k]),
                             kept_components=sorted(k for k in new if old.get(k) == new[k])))

    for field in state.awaiting_fields:
        spec = run.questions.get(field)
        events.append(_event("question", field=field, text=run.texts.get(field, spec.why if spec else field),
                             options=list(spec.options) if spec else [], allow_skip=bool(spec and spec.allow_skip)))

    if run.explanation and state.run_status != RunStatus.INTERRUPTED:
        # Текст объяснения прошёл number guard рантайма (или заменён шаблоном из состояния);
        # отказ `infeasible` собирает код из нарушений — числа в обоих случаях из state.
        events.append(_event("answer_text", block_id="explain", text=run.explanation, numbers_verified=True))
    if state.outcome == Outcome.BEST_EFFORT:
        limits = [u.split(":", 1)[1] for u in run.unresolved if u.startswith("limit:")]
        events.append(_event("budget_exhausted", limit_hit=limits[0] if limits else "unknown",
                             best_effort_version=before.version + 1,
                             unresolved=[u for u in run.unresolved if not u.startswith("limit:")]))
    return events


class AgentRunExecutor:
    """Один `Runtime` на процесс: его кэш и черновики trip'ов живут между ходами."""

    def __init__(self, sources: Mapping[str, Source], slots: Any | None = None) -> None:
        self._recorder = _Recorder()
        wrapped = {name: self._recorder.wrap(name, source) for name, source in sources.items()}
        self._runtime = Runtime(wrapped, slots=slots)
        # Runtime не потокобезопасен, а store гонит ходы через asyncio.to_thread.
        self._lock = threading.Lock()

    def execute(self, state: TripState, turn: RunInput) -> RunResult:
        with self._lock:
            self._recorder.calls = []
            if turn.kind == "message":
                if not turn.text:
                    raise InvalidAnswer("Пустое сообщение")
                run = self._runtime.start(turn.text, state=state)
                trace_from = 0
            else:
                if turn.resume is None or turn.field is None:
                    raise InvalidAnswer("Ход нельзя продолжить: нет сохранённого состояния агента")
                run = self._runtime.load(turn.resume)
                trace_from = len(run.trace)
                try:
                    run = self._runtime.answer(run, turn.field, turn.value or "")
                except ValueError as exc:
                    raise InvalidAnswer(f"Не получилось прочитать ответ: {exc}") from exc
            events = build_events(state, run, trace_from, self._recorder.calls)
        # Store ведёт версии по ходам HTTP: агент внутри хода поднимает свою много раз.
        next_state = run.state.model_copy(update={"version": state.version + 1})
        resume = self._runtime.dump(run) if next_state.run_status == RunStatus.INTERRUPTED else None
        logger.info("agent turn trip=%s kind=%s outcome=%s events=%d", state.trip_id, turn.kind,
                    next_state.outcome, len(events))
        return RunResult(state=next_state, events=tuple(events), resume=resume)


def agent_executor_from_env() -> AgentRunExecutor:
    """Каталог `data/seeds` + слоты из окружения (`LLM_PROVIDER=mock` — бесплатно и офлайн)."""

    from services.agent.app.llm.provider_openai_compatible import slots_from_config
    from services.agent.app.tools.seed_sources import seed_sources

    sources: dict[str, Callable[[Mapping[str, object]], Any]] = dict(seed_sources())
    slots = slots_from_config()
    logger.info("agent executor ready provider=%s slots=%s", os.environ.get("LLM_PROVIDER", "mock"),
                type(slots).__name__)
    return AgentRunExecutor(sources, slots=slots)
