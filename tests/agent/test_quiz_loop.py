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
from shared.contracts.enums import DirectionMode, Stage, Theme
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

    def test_month_without_a_date_is_inferred_not_user(self):
        request = mock_parse("уедем в ноябре на неделю")
        assert request.dates.start.status.value == "inferred"
        assert request.dates.start.source == "context"
        assert request.dates.start.needs_confirmation is True

    def test_pet_phrase_marks_hard_known_field(self):
        request = mock_parse("едем с собакой")
        assert request.party.pets.value is True
        assert request.party.pets.kind.value == "hard"
        assert request.party.pets.source == "user"

    def test_mock_does_not_invent_what_the_user_did_not_say(self):
        request = mock_parse("хочу куда-нибудь на неделю")
        user_said = {path for path, field in request.iter_fields()
                     if getattr(field, "source", None) == "user"}
        assert user_said == set()
        assert request.themes.is_unknown
        assert request.party.adults.status.value == "defaulted"
        assert request.direction.mode.status.value == "defaulted"

    def test_words_the_user_did_say_stay_known(self):
        request = mock_parse("вдвоём к морю")
        assert request.themes.value == [Theme.BEACH]
        assert request.party.adults.value == 2
        assert request.party.adults.source == "user"

    def test_named_destination_is_not_dropped(self):
        """«еду в Сочи» — hard-поле со слов юзера; раньше парсер его молча терял."""

        request = mock_parse("еду в сочи на неделю")
        assert request.direction.explicit.value == "сочи"
        assert request.direction.explicit.source == "user"
        assert request.direction.mode.value == DirectionMode.EXPLICIT

    def test_month_or_theme_is_not_a_destination(self):
        for phrase in ("поеду в ноябре", "хочу на море вдвоём", "лечу в отпуск",
                       "еду в горы", "хочу в аэропорт"):
            request = mock_parse(phrase)
            assert request.direction.explicit.is_unknown, phrase
            assert request.direction.mode.status.value == "defaulted", phrase

    def test_hyphenated_city_is_captured_whole(self):
        request = mock_parse("лечу в санкт-петербург")
        assert request.direction.explicit.value == "санкт-петербург"

    def test_golden_message_names_no_place(self):
        request = mock_parse(MESSAGE)
        assert request.direction.explicit.is_unknown
        assert request.direction.mode.status.value == "defaulted"
