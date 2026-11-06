"""replanning layer: дельта пересобирает только необходимое и остаётся офлайн."""

from __future__ import annotations

import pytest

from services.agent.app.orchestration.runtime import Runtime
from services.agent.app.llm.slots import MockSlots
from shared.contracts.enums import EscalationStep, Outcome

from tests.scenarios.test_golden_path import NOW, SOURCES, golden_run


@pytest.fixture
def ready_runtime() -> tuple[Runtime, object]:
    runtime = Runtime(SOURCES)  # type: ignore[arg-type]
    return runtime, golden_run(runtime)


@pytest.mark.parametrize(
    ("message", "step", "source_calls"),
    [
        ("давай на троих", EscalationStep.RECOMPOSE, 0),
        ("у нас собака", EscalationStep.RE_SEARCH, 5),
        ("бюджет -30%", EscalationStep.RE_RANK, 0),
        ("на два дня позже", EscalationStep.RE_SEARCH, 5),
    ],
)
def test_delta_has_bounded_replan_and_keeps_old_components(
    ready_runtime: tuple[Runtime, object], message: str, step: EscalationStep, source_calls: int,
) -> None:
    runtime, previous = ready_runtime
    before = runtime.cache.source_calls
    run = runtime.start(message, state=previous.state, now=NOW)  # type: ignore[attr-defined]

    assert run.replan_step == step
    assert runtime.cache.source_calls - before == source_calls
    assert run.kept_components  # UI can show what survived the old plan
    assert run.state.budgets.wall_clock_ms >= 0
    assert run.state.budgets.tool_calls == source_calls


def test_budget_replan_never_reopens_source_and_is_explained(
    ready_runtime: tuple[Runtime, object],
) -> None:
    runtime, previous = ready_runtime
    before = runtime.cache.source_calls
    run = runtime.start("бюджет -30%", state=previous.state, now=NOW)  # type: ignore[attr-defined]

    assert runtime.cache.source_calls == before
    assert run.replan_step == EscalationStep.RE_RANK
    assert run.state.outcome == Outcome.INFEASIBLE
    assert run.state.repair_log
    assert run.state.repair_log[0].iteration == 1
    assert "REPAIR:0:BUDGET_EXCEEDED:plan" in run.trace


def test_successful_replan_calls_offline_diff_slot(
    ready_runtime: tuple[Runtime, object],
) -> None:
    runtime, previous = ready_runtime
    run = runtime.start("давай на троих", state=previous.state, now=NOW)  # type: ignore[attr-defined]

    assert run.state.outcome == Outcome.PLAN_READY
    assert run.diff_text
    assert run.trace[-1].startswith("DIFF:versions=")


def test_excluded_city_is_not_written_into_explicit_direction() -> None:
    request = MockSlots().parse("хочу в Турцию, но не в Анталью")

    assert request.direction.explicit.value == "турцию"
    assert request.direction.excluded.value == ["анталью"]
