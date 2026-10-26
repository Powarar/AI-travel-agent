"""Викторина: цикл PARSE → DECIDE → question (interrupt) → answer → SEARCH.

Mock-парсер вместо LLM (слоты подключаются в T-07); контракт вопросов — тот же, что
уйдёт на фронт SSE-событием `question` (services/agent/docs/CONTRACTS.md).

Запуск:
    python services/agent/scripts/quiz_demo.py                 # интерактивно
    python services/agent/scripts/quiz_demo.py --non-interactive --answers "Сочи,14,21"
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from shared.contracts.enums import (
    ConstraintKind,
    DirectionMode,
    FieldStatus,
    RunStatus,
    Stage,
    Theme,
)
from shared.contracts.money import Money
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import TripState

DEMO_MONTH = date(2026, 11, 1)
NO_NETWORK_NOTE = "внешних HTTP-вызовов в этом контуре нет"

QUESTIONS: dict[str, dict[str, object]] = {
    "origin": {
        "field": "origin",
        "text": "Откуда стартуем? Нужен город или аэропорт вылета.",
        "options": ["Москва", "Санкт-Петербург", "Екатеринбург"],
        "allow_skip": False,
    },
    "dates.start": {
        "field": "dates.start",
        "text": "Даты в ноябре: назовите вилку (например, «с 14 по 21») или пропустите —",
        "options": ["с 10 по 17", "с 14 по 21", "с 20 по 27"],
        "allow_skip": True,
    },
}


_ADULTS_WORDS = {
    "вдвоём": 2, "для двоих": 2, "нас двое": 2, "мы двое": 2,
    "втроём": 3, "для троих": 3, "нас трое": 3,
}
_THEME_WORDS = {
    Theme.BEACH: ("морю", "море", "пляж"),
    Theme.NATURE: ("гор", "лес", "природа"),
    Theme.CITY: ("город",),
    Theme.CULTURE: ("музе", "культур", "истори"),
    Theme.FOOD: ("гастро", "еда", "ресторан"),
}


def mock_parse(text: str) -> TripRequest:
    """Слот №1 (Parse) на моке: значение + kind + status + source.

    Мок не имеет права приписывать юзеру то, чего он не сказал: unmatched поле остаётся
    `unknown`, а безопасная подстановка — `defaulted`, иначе `known` только по тексту.
    """

    request = TripRequest()
    lowered = text.lower()

    request.direction.mode = FieldValue[DirectionMode].defaulted(
        DirectionMode.OPEN, kind=ConstraintKind.HARD
    )
    themes = [theme for theme, words in _THEME_WORDS.items() if any(word in lowered for word in words)]
    if themes:
        request.themes = FieldValue[list[Theme]].known(themes, kind=ConstraintKind.SOFT, source="user")
    request.soft_preferences = [
        phrase.strip()
        for phrase in re.findall(r"(без долгих перелётов|не долго лететь|тёплому морю)", lowered)
    ]

    hits = [count for word, count in _ADULTS_WORDS.items() if word in lowered]
    adults = hits[0] if hits else None
    if adults is None:
        request.party.adults = FieldValue[int].defaulted(2, kind=ConstraintKind.HARD)
    else:
        request.party.adults = FieldValue[int].known(adults, kind=ConstraintKind.HARD, source="user")
    if re.search(r"с ребёнком", lowered):
        request.party.children = FieldValue[list[int]].known([7], kind=ConstraintKind.HARD, source="user")
    if re.search(r"с собако|с кот", lowered):
        request.party.pets = FieldValue[bool].known(True, kind=ConstraintKind.HARD, source="user")

    if match := re.search(r"(\d{1,3})\s*[кk]\b", lowered):
        amount = Decimal(match.group(1)) * 1000
        request.budget.amount = FieldValue[Money].known(
            Money(amount=amount, currency="RUB"), kind=ConstraintKind.HARD, source="user"
        )
    if match := re.search(r"не больше (\d{1,2})\s*(?:час|ч)\b", lowered):
        request.transport.max_hours_direct = FieldValue[float].known(
            float(match.group(1)), kind=ConstraintKind.HARD, source="user"
        )
    if "прям" in lowered and ("рейс" in lowered or "перел" in lowered):
        request.transport.prefer_direct = FieldValue[bool].known(True, kind=ConstraintKind.SOFT, source="user")

    if "ноябр" in lowered:
        # «в ноябре» — это не дата: вывели мы, значит поле требует подтверждения (CONTRACTS.md)
        request.dates.start = FieldValue[date].inferred(
            DEMO_MONTH.replace(day=1), kind=ConstraintKind.HARD, source="context", confidence=0.5
        )

    if match := re.search(r"(\d{1,2})\s*(?:ноч|ночей)", lowered):
        request.nights = FieldValue[int].known(int(match.group(1)), kind=ConstraintKind.HARD, source="user")

    return request


def decide_questions(state: TripState) -> list[dict[str, object]]:
    """Порог значимости (ThresholdProvider): unknown hard-поля, без которых нечего искать."""

    if state.questions_asked >= state.budgets.questions_limit:
        return []
    origin_unknown = state.request.origin.is_unknown
    start = state.request.dates.start
    dates_significant = start.is_unknown or (
        start.status == FieldStatus.INFERRED and start.needs_confirmation
    )
    asks: list[dict[str, object]] = []
    if origin_unknown:
        asks.append(QUESTIONS["origin"])
    if dates_significant and len(asks) < 2:
        asks.append(QUESTIONS["dates.start"])
    return asks[: 2 - state.questions_asked]


def apply_answer(request: TripRequest, field: str, raw: str) -> TripRequest:
    answer = raw.strip()
    ask = QUESTIONS[field]
    options = [str(option) for option in ask["options"]]
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        answer = options[int(answer) - 1]
    skipped = answer.lower() in {"", "пропустить", "skip", "-"}

    if field == "origin":
        if skipped:
            raise ValueError("origin is hard-required: skipping is not allowed")
        request.origin = FieldValue[str].known(answer, kind=ConstraintKind.HARD, source="user")
        return request

    if field == "dates.start":
        if skipped:
            request.dates.start = FieldValue[date].defaulted(
                DEMO_MONTH.replace(day=14), kind=ConstraintKind.HARD
            )
            request.dates.end = FieldValue[date].defaulted(
                DEMO_MONTH.replace(day=21), kind=ConstraintKind.HARD
            )
            request.dates.flexibility_days = FieldValue[int].defaulted(3, kind=ConstraintKind.SOFT)
            request.nights = FieldValue[int].defaulted(7, kind=ConstraintKind.HARD)
        else:
            iso = re.findall(r"\d{4}-\d{2}-\d{2}", answer)
            if len(iso) >= 2:
                start, end = date.fromisoformat(iso[0]), date.fromisoformat(iso[1])
            else:
                days = [int(n) for n in re.findall(r"\d{1,2}", answer)]
                if len(days) < 2:
                    raise ValueError(f"cannot read a date range from {answer!r}")
                start = DEMO_MONTH.replace(day=days[0])
                end = DEMO_MONTH.replace(day=days[1])
            if end <= start:
                raise ValueError(f"date range is empty: {start} → {end}")
            request.dates.start = FieldValue[date].known(start, kind=ConstraintKind.HARD, source="user")
            request.dates.end = FieldValue[date].known(end, kind=ConstraintKind.HARD, source="user")
            request.nights = FieldValue[int].inferred(
                (end - start).days, kind=ConstraintKind.HARD, source="dates", confidence=1.0
            )
        return request

    raise ValueError(f"no such question: {field}")


def run_turn(state: TripState, message: str, *, interactive: bool) -> TripState:
    """Один ход контура. Возвращает состояние; stage=CLARIFY означает interrupt."""

    state.budgets.llm_calls += 1  # mock-слот Parse
    state.request = mock_parse(message) if state.stage == Stage.PARSE else state.request
    state.stage = Stage.DECIDE

    asks = decide_questions(state)
    if not asks:
        state.stage = Stage.SEARCH
        state.budgets.tool_calls += 3
        return state

    for ask in asks:
        field = str(ask["field"])
        if interactive:
            print(f"\n  ❓ {ask['text']}")
            for index, option in enumerate(ask["options"], 1):
                print(f"     [{index}] {option}")
            print("     [enter] пропустить" if ask["allow_skip"] else "     пропуск нельзя")
            raw = input(f"  ответ на «{field}»: ")
        else:
            raw = ""
        state = resume_with_answer(state, field, raw)
    return state


def resume_with_answer(state: TripState, field: str, raw: str) -> TripState:
    """POST /v1/runs/{id}/answers: тот же ход, идемпотентно по (run_id, field)."""

    state.request = apply_answer(state.request, field, raw)
    state.questions_asked += 1
    state.budgets.questions_asked = state.questions_asked
    state.awaiting_fields = [f for f in state.awaiting_fields if f != field]
    remaining = decide_questions(state)
    if remaining:
        state.stage = Stage.CLARIFY
        state.awaiting_fields = [str(ask["field"]) for ask in remaining]
        return state
    state.stage = Stage.SEARCH
    state.budgets.tool_calls += 3
    return state


def render_state(state: TripState) -> None:
    print(f"\n--- TripState (version={state.version}, stage={state.stage.value}) ---")
    for name, field in state.request.iter_fields():
        if isinstance(field, FieldValue) and not field.is_unknown:
            value = field.value.value if isinstance(field.value, DirectionMode) else field.value
            if isinstance(field.value, Money):
                value = field.value.as_display()
            print(f"  {name:<28} = {value!s:<16} [{field.kind.value}, {field.status.value}, source={field.source}]")
    print(f"\n  вопросов задано: {state.questions_asked}, tool-вызовов: {state.budgets.tool_calls}, llm-вызовов: {state.budgets.llm_calls}")
    print(f"  лимит хода: questions={state.budgets.questions_limit}, tool={state.budgets.tool_calls_limit}, llm={state.budgets.llm_calls_limit}")
    print(f"  {NO_NETWORK_NOTE}")


def persist(state: TripState, path: str) -> None:
    """run(status=interrupted, awaiting=field[]) в БД; здесь — файл, чтобы показать
    что ход переживает рестарт процесса."""

    state.run_status = RunStatus.INTERRUPTED
    with open(path, "w") as handle:
        handle.write(state.model_dump_json(indent=2))


def resume_from_file(path: str, answers: list[tuple[str, str]]) -> TripState:
    with open(path) as handle:
        state = TripState.model_validate_json(handle.read())
    print(f"возобновлены из файла: trip={state.trip_id}, awaiting={state.awaiting_fields}")
    for field, raw in answers:
        if state.stage == Stage.SEARCH:
            break
        state = resume_with_answer(state, field, raw)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--message", default="хочу к тёплому морю в ноябре вдвоём с ребёнком, 150к, без долгих перелётов, не больше 4 ч в дороге")
    parser.add_argument("--answers", default="Москва;пропустить", help="через ; для --non-interactive")
    parser.add_argument("--interrupt-file", help="сохранить прерванный ход в файл")
    parser.add_argument("--resume", help="возобновить ход из файла")
    parser.add_argument("--resume-answers", default="origin=Москва;dates.start=с 14 по 21")
    args = parser.parse_args()

    if args.resume:
        answers = [tuple(item.split("=", 1)) for item in args.resume_answers.split(";")]
        render_state(resume_from_file(args.resume, list(answers)))
        return 0

    state = TripState(trip_id=uuid4(), version=1)
    print(f"запрос: «{args.message}»")

    if args.non_interactive:
        answers = iter(args.answers.split(";"))
        state.budgets.llm_calls += 1  # mock-слот Parse
        state.request = mock_parse(args.message)
        state.stage = Stage.DECIDE
        asks = decide_questions(state)
        for index, ask in enumerate(asks):
            if args.interrupt_file and index == len(asks) - 1:
                state.stage = Stage.CLARIFY
                state.awaiting_fields = [str(ask["field"])]
                persist(state, args.interrupt_file)
                print(f"  interrupt: ход сохранён в {args.interrupt_file}, awaiting={state.awaiting_fields}")
                render_state(state)
                return 0
            state = resume_with_answer(state, str(ask["field"]), next(answers, ""))
    else:
        state = run_turn(state, args.message, interactive=True)

    render_state(state)
    if state.stage == Stage.SEARCH:
        print("\n→ SEARCH: suggest_destinations, transport_search, weather_climate (в T-05 — по реестру)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
