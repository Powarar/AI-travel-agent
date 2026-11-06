"""Russian UX regressions: technical exceptions must not reach chat."""

from services.backend.app.api.agent_executor import _answer_error


def test_date_answer_error_is_actionable_and_russian() -> None:
    message = _answer_error(ValueError("cannot read a date range from '7-11.11'"))

    assert message == "Не удалось распознать даты. Напишите диапазон, например: «с 7 по 11 ноября»."
    assert "cannot read" not in message


def test_unknown_answer_error_does_not_leak_internal_details() -> None:
    message = _answer_error(ValueError("database host leaked.example is unavailable"))

    assert message == "Не удалось обработать ответ. Попробуйте сформулировать его иначе."
    assert "leaked.example" not in message
