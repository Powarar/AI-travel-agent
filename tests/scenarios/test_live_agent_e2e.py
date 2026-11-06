"""Explicitly billable acceptance checks for the live model over the local seed catalogue.

They exercise the same Runtime and provider boundary used by the backend executor. They
do not claim to validate real inventory: flights and hotel availability remain seed data
until a provider adapter is implemented.
"""

from __future__ import annotations

import os

import pytest

from scripts.run_pipeline import FIXTURE_NOW, TurnReport, walk
from services.agent.app.llm.provider_openai_compatible import OpenAICompatibleSlots, load_env_file
from services.agent.app.orchestration.runtime import Runtime
from services.agent.app.tools.seed_sources import seed_sources
from shared.contracts.enums import Outcome

load_env_file()


def _provider() -> OpenAICompatibleSlots | None:
    try:
        candidate = OpenAICompatibleSlots.from_config()
    except ValueError:
        return None
    return candidate if isinstance(candidate, OpenAICompatibleSlots) else None


LIVE_ENABLED = os.environ.get("RUN_LIVE_EVALS") == "1" and _provider() is not None


@pytest.mark.live
@pytest.mark.skipif(not LIVE_ENABLED, reason="live checks require RUN_LIVE_EVALS=1 and a configured provider")
@pytest.mark.parametrize(
    ("message", "answers", "expected", "required_text"),
    [
        (
            "Хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, бюджет 150 000 рублей.",
            {},
            Outcome.PLAN_READY,
            "Сочи",
        ),
        (
            "Хочу к тёплому морю в ноябре вдвоём, бюджет 200 000 рублей.",
            {"origin": "Москва", "dates.start": "7-11.11"},
            Outcome.INFEASIBLE,
            "Даже без проживания",
        ),
        (
            "Хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, бюджет 10 000 рублей.",
            {},
            Outcome.INFEASIBLE,
            "Даже без проживания",
        ),
    ],
)
def test_live_agent_respects_russian_contracts(
    message: str, answers: dict[str, str], expected: Outcome, required_text: str,
) -> None:
    provider = _provider()
    assert provider is not None
    report = TurnReport()
    runtime = Runtime(seed_sources(), slots=provider)
    try:
        run = walk(runtime, message, answers, now=FIXTURE_NOW, ask=lambda path, _: answers[path], report=report)
        assert run.state.outcome is expected
        assert required_text.lower() in report.explanation_text.lower()
        assert "cannot read" not in report.explanation_text.lower()
        assert provider.failures == [], provider.failures
        if run.state.outcome is Outcome.PLAN_READY:
            assert report.invented_numbers == []
    finally:
        provider.close()
