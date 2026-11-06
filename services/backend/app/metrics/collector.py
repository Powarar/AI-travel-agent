"""Small dependency-free collector; replaceable by OTEL/Prometheus at deployment."""
from __future__ import annotations

from collections import Counter
from threading import Lock

from shared.contracts.enums import Outcome, RunStatus
from shared.contracts.state import TripState

from .schemas import MonitoringSummary, OutcomeMetrics, QualityMetrics


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._outcomes: Counter[str] = Counter()
        self._tool_errors: Counter[str] = Counter()
        self._runs = self._wall = self._llm = self._tools = self._questions = self._fallbacks = 0

    def record(self, state: TripState, *, tool_errors: list[str], fallbacks: int) -> None:
        with self._lock:
            self._runs += 1
            self._wall += state.budgets.wall_clock_ms
            self._llm += state.budgets.llm_calls
            self._tools += state.budgets.tool_calls
            self._questions += state.budgets.questions_asked
            self._fallbacks += fallbacks
            if state.run_status is RunStatus.INTERRUPTED:
                self._outcomes["awaiting_answer"] += 1
            elif state.outcome is not None:
                self._outcomes[state.outcome.value] += 1
            for name in tool_errors:
                self._tool_errors[name] += 1

    def summary(self) -> MonitoringSummary:
        with self._lock:
            n = self._runs
            outcomes = OutcomeMetrics(runs=n, fallbacks=self._fallbacks, **{
                key: self._outcomes[key] for key in ("plan_ready", "infeasible", "best_effort", "unsupported", "awaiting_answer")
            })
            return MonitoringSummary(
                outcomes=outcomes,
                avg_wall_clock_ms=round(self._wall / n, 1) if n else 0,
                avg_llm_calls=round(self._llm / n, 2) if n else 0,
                avg_tool_calls=round(self._tools / n, 2) if n else 0,
                avg_questions=round(self._questions / n, 2) if n else 0,
                tool_errors=dict(self._tool_errors),
                quality=QualityMetrics(),
            )
