"""Викторина (кусок T-05): ≤2 вопроса, interrupt переживает рестарт, hard-поле нельзя пропустить."""

import json
from pathlib import Path

import pytest

from services.agent.scripts.quiz_demo import (
    apply_answer,
    decide_questions,
    mock_parse,
    resume_from_file,
    resume_with_answer,
)
from shared.contracts.enums import Stage
from shared.contracts.state import TripState

MESSAGE = "хочу к тёплому морю в ноябре вдвоём с ребёнком, 150к, без долгих перелётов"


def fresh_state() -> TripState:
    state = TripState(trip_id="0f9c6b1e-0000-4000-8000-000000000001", version=1)
    state.request = mock_parse(MESSAGE)
    state.stage = Stage.DECIDE
    return state


class TestQuestionLimit:
    def test_at_most_two_questions(self):
        asks = decide_questions(fresh_state())
        assert len(asks) <= 2
        assert [ask["field"] for ask in asks] == ["origin", "dates.start"]

    def test_no_questions_after_limit(self):
        state = fresh_state()
        state.questions_asked = 2
        assert decide_questions(state) == []

    def test_hard_field_cannot_be_skipped(self):
        with pytest.raises(ValueError, match="hard-required"):
            apply_answer(fresh_state().request, "origin", "пропустить")

    def test_skipped_soft_date_becomes_defaulted_not_known(self):
        request = apply_answer(fresh_state().request, "dates.start", "пропустить")
        assert request.dates.start.source == "default"
        assert request.nights.source == "default"


class TestInterrupt:
    def test_run_survives_process_restart(self, tmp_path: Path):
        state = fresh_state()
        state = resume_with_answer(state, "origin", "Сочи")
        assert state.stage == Stage.CLARIFY
        assert state.awaiting_fields == ["dates.start"]

        path = tmp_path / "interrupted.json"
        path.write_text(json.dumps(json.loads(state.model_dump_json()), ensure_ascii=False))
        restored = resume_from_file(str(path), [("dates.start", "с 20 по 27")])
        assert restored.stage == Stage.SEARCH
        assert restored.questions_asked == 2
        assert str(restored.request.origin.value) == "Сочи"

    def test_answer_marks_source_user_on_hard_field(self):
        state = resume_with_answer(fresh_state(), "origin", "Москва")
        assert state.request.origin.source == "user"
        assert state.request.origin.status.value == "known"

    def test_iso_date_pair_is_not_misparsed_as_year_digits(self):
        state = resume_with_answer(fresh_state(), "dates.start", "2026-11-01:2026-11-08")
        assert str(state.request.dates.start.value) == "2026-11-01"
        assert state.request.nights.value == 7

    def test_empty_date_range_is_rejected(self):
        with pytest.raises(ValueError):
            apply_answer(fresh_state().request, "dates.start", "с 27 по 20")
