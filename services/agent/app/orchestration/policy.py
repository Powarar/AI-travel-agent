"""Рёбра контура: какой узел следующий, что спросить, когда ход пора останавливать.

Три вещи, которые здесь решаются кодом (инвариант №3 и `docs/LLM-BOUNDARY.md`: «хватает ли
данных искать» и «какие сервисы звать» — не модель):

* `next_stage` — фазы из `docs/ARCHITECTURE.md` §2, включая `CLARIFY` как паузу;
* `questions_to_ask` — какие unknown-поля значимы для поиска;
* `question_for_blocking` — что спросить, когда hard-ограничение не выполняется (инвариант №2).

Лимиты хода (`≤6 LLM, ≤12 tool, ≤20 с`) считает `RunBudget.exhausted()` из контракта, а не
копия этих чисел здесь; останавливает ход рантайм, и остановка — исход `best_effort`.

Лимит вопросов — единственная граница, которая ход не рвёт: когда спрашивать уже нельзя,
рантайм идёт дальше с тем, что есть, а поля остаются со статусом `unknown`/`defaulted` и
видны в UI. Так ведёт себя и `scripts/quiz_demo.py`. Считается он накопленно по путешествию
(`TripState.questions_asked`), поэтому лимит не обходится серией коротких сообщений.

`severity` понизить здесь нельзя: blocking-нарушение не превращается в «и так сойдёт»
(инвариант №2). Его исход — вопрос с посчитанными числами или `infeasible`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import (
    ConstraintKind,
    DirectionMode,
    FieldStatus,
    Outcome,
    Severity,
    Stage,
)
from shared.contracts.months import month_label
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import TripState
from shared.contracts.violation import Violation

#: Поля, без которых поиск даст неверный результат: их надо спрашивать.
#: `party.adults` и `direction.mode` в списке отсутствуют — на unknown они закрываются
#: безопасным `defaulted` (CONTRACTS.md), и вопрос только потратил бы лимит.
#: За вопросами про режим дело дошло до живого контура 2026-10-27: unknown-режим забирал
#: второй вопрос хода, а ответ «конкретная страна» ставил `explicit` без названия города —
#: то есть поиск без направления и без INSPIRE. Молча спрашивать об этом нельзя.
SIGNIFICANT: tuple[str, ...] = ("origin", "dates.start", "budget.amount")

#: Больше двух спецификаций за один вызов не отдаём: столько же и разрешено на путешествие,
#: поэтому список ниже лимита и `MAX_QUESTIONS_IN_FLIGHT` — просто граница «не расплодить».
MAX_QUESTIONS_IN_FLIGHT = 2


class QuestionSpec(BaseModel):
    """Вопрос как данные: код решает поле и числа, модель даёт формулировку (слот №2).

    Pydantic-модель, а не dataclass: спецификация уходит в сериализованный прерванный ход
    (`runtime.InterruptedRun`) и в SSE-событие `question` (T-06), поэтому она должна
    выгружаться и загружаться без ручной сборки полей.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    why: str
    options: tuple[str, ...] = ()
    allow_skip: bool = False
    evidence: dict[str, str] = Field(default_factory=dict)


def next_stage(state: TripState) -> Stage | None:
    """Ребро контура: по завершённой фазе (`state.stage`) — какая следующая.

    `None` — терминал: ход закончен (или стоит на паузе `CLARIFY` и ждёт внешний вход).
    Узлы фазу не выбирают: `nodes` отмечает выполненную фазу, решение «что дальше» здесь.
    """

    stage = state.stage
    if stage == Stage.PARSE:
        return Stage.DECIDE
    if stage == Stage.DECIDE:
        if state.awaiting_fields:
            return Stage.CLARIFY
        return Stage.INSPIRE if _needs_destinations(state) else Stage.SEARCH
    if stage == Stage.CLARIFY:
        return None                       # interrupt: ждём внешний вход
    if stage == Stage.INSPIRE:
        return Stage.SEARCH
    if stage == Stage.SEARCH:
        return Stage.COMPOSE
    if stage == Stage.COMPOSE:
        return Stage.VALIDATE
    if stage == Stage.VALIDATE:
        if state.awaiting_fields:
            return Stage.CLARIFY
        return None if state.blocking_violations() else Stage.SHORTLIST
    if stage == Stage.SHORTLIST:
        return Stage.EXPLAIN
    if stage == Stage.EXPLAIN:
        return Stage.PLAN
    return None                            # PLAN, CHECKOUT, REPAIR (T-08) — терминал


def _needs_destinations(state: TripState) -> bool:
    """INSPIRE только когда направление открыто и финалистов ещё нет (§2).

    На `mode == explicit` фаза не выполняется: направление не ищется, если его просили
    задать. Такой сценарий в этом блоке не открывается — золотой путь и все проверки идут
    через «куда-нибудь к тёплому морю».
    """

    if state.request.direction.mode.value != DirectionMode.OPEN:
        return False
    return not any(candidate.ref.get("finalist") == "true" for candidate in state.candidates.destinations)


def _can_ask(state: TripState) -> bool:
    """Спрашивать можно, пока не исчерпан лимит вопросов на путешествие.

    Считается накопленный `TripState.questions_asked`, а не счётчик хода: обещание
    «не больше двух вопросов» пользователь получает на всю поездку, а не на каждый
    `POST /messages`. `RunBudget.questions_limit` здесь — источник самого числа.
    """

    return state.questions_asked < state.budgets.questions_limit


def questions_to_ask(state: TripState) -> list[QuestionSpec]:
    """Значимые unknown → не больше двух, и не больше лимита на путешествие."""

    if not _can_ask(state):
        return []
    request = state.request
    specs: list[QuestionSpec] = []
    for path in SIGNIFICANT:
        held = request.field_value(path)
        if not isinstance(held, FieldValue) or not _is_significant_unknown(held):
            continue
        if path == "dates.start":
            end = request.dates.end
            #: «вилка уже названа» — только если конец назван словами или подставлен кодом.
            #: Вывод модели (`inferred` + на подтверждение) вилкой не считается: живой путь
            #: 2026-10-27 отдал start=end из «в ноябре», вопрос про даты молча пропустили,
            #: и поездка нулевой длины ушла в сборку.
            if not end.is_unknown and not _is_significant_unknown(end):
                continue
        specs.append(_spec_for(path, request))
        if len(specs) == MAX_QUESTIONS_IN_FLIGHT:
            break
    return specs


def _is_significant_unknown(held: FieldValue[Any]) -> bool:
    if held.status == FieldStatus.UNKNOWN:
        return True
    return held.status == FieldStatus.INFERRED and held.needs_confirmation


def _spec_for(path: str, request: TripRequest) -> QuestionSpec:
    """Поле + почему оно нужно + варианты ответа; текст даёт слот №2."""

    if path == "origin":
        return QuestionSpec(
            field="origin",
            why="Нужен город или аэропорт вылета — без него нечего искать в перелётах",
            options=("Москва", "Санкт-Петербург", "Екатеринбург"),
            allow_skip=False,
        )
    if path == "dates.start":
        month = request.dates.start.value.month if request.dates.start.value else None
        #: месяц по-русски, а не «в 11-м месяце»: вопрос читает человек, а номер — это ключ
        #: источника (`dates.month`), показывать его пользователю не за чем
        named = month_label(month)
        hint = f"в {named}" if named else "когда едем"
        return QuestionSpec(
            field="dates.start",
            why=f"Назовите вилку {hint} — например «с 14 по 21», или пропустите",
            options=("с 10 по 17", "с 14 по 21", "с 20 по 27"),
            allow_skip=True,
        )
    if path == "budget.amount":
        return QuestionSpec(
            field="budget.amount",
            why="Сколько готовы потратить на поездку целиком — сумма определяет отель и перелёт",
            options=("120000", "150000", "180000"),
            allow_skip=False,
        )
    return QuestionSpec(
        field="direction.mode",
        why="Куда едем: назовите направление или разрешите подобрать тёплое море",
        options=("подбери сам", "конкретная страна"),
        allow_skip=True,
    )


def question_for_blocking(state: TripState) -> QuestionSpec | None:
    """Hard-нарушение, которое не чинится пересчётом, — это вопрос, а не ослабление.

    `repairable=True` оставлен T-08: в этом блоке пересборку-чинимку не играем, такой
    терминал — `infeasible` с посчитанными числами.
    """

    if not _can_ask(state):
        return None
    for violation in state.blocking_violations():
        if violation.repairable or violation.field_path is None:
            continue
        held = state.request.field_value(violation.field_path)
        if not isinstance(held, FieldValue) or held.kind != ConstraintKind.HARD:
            continue
        if held.source != "user":
            continue
        return _ask_about(violation, state)
    return None


def _ask_about(violation: Violation, state: TripState) -> QuestionSpec:
    path = violation.field_path or ""
    if path == "transport.max_hours_direct":
        closest = _shortest_flight_hours(state)
        options = (closest,) if closest is not None else ()
        return QuestionSpec(
            field=path,
            why=(
                f"Ты просил не больше {violation.expected} в дороге, "
                f"а самый короткий перелёт — {violation.actual}"
            ),
            options=options,
            allow_skip=False,
            evidence={"expected": violation.expected or "", "actual": violation.actual or ""},
        )
    if path == "direction.explicit":
        # Формулировка для человека, а не имя поля (CONTRACTS.md, «Названное направление»):
        # ответ здесь ровно двух видов — другое название или «подбери сам», и оба должны
        # читаться из варианта. Число рассмотренных направлений берётся из `evidence`
        # валидатора: узел решения их не пересчитывает и не выдумывает, а слот №2 кладёт
        # пары «метка → число» в текст вопроса как есть, поэтому метки — русские.
        considered = violation.evidence.get("considered", "")
        return QuestionSpec(
            field=path,
            why=f"Ты просил «{violation.expected}», а план выходит в «{violation.actual}».",
            options=("подбери сам",),
            allow_skip=False,
            evidence={"рассмотрено направлений": considered} if considered else {},
        )
    return QuestionSpec(
        field=path,
        why=f"Ограничение «{path}» не выполняется: ждали {violation.expected}, вышло {violation.actual}",
        evidence=dict(violation.evidence),
        allow_skip=False,
    )


def _shortest_flight_hours(state: TripState) -> str | None:
    """Самый короткий перелёт туда-обратно — по той же паре сегментов, что и в сборке.

    `nodes.compose` сравнивает с ограничением максимум из двух сегментов, поэтому и вариант
    ответа обязан быть таким же: «3.5 ч» при посчитанном «4.0 ч» было бы числом, которого в
    плане нет, и пользователь выбрал бы то, чего не существует (инвариант №3).
    """

    per_destination: dict[str, list[float]] = {}
    for candidate in state.candidates.transport:
        hours = candidate.ref.get("duration_h")
        if hours is None:
            continue
        per_destination.setdefault(candidate.ref.get("destination", ""), []).append(float(hours))
    if not per_destination:
        return None
    return f"{min(max(hours) for hours in per_destination.values())}"


def blocking_actions(violations: list[Violation]) -> list[str]:
    """Что бы чинил repair (T-08) — уходит в `unresolved` и в `budget_exhausted`."""

    return sorted({f"{violation.code.value}:{action}"
                   for violation in violations for action in violation.suggested_actions})


def terminal_outcome(violations: list[Violation]) -> Outcome:
    """Без repair (T-08) допустимое множество под hard-нарушением пусто → `infeasible`.

    Числа, объясняющие отказ, остаются в `Violation.expected/actual/evidence` — их берёт код,
    и «не нашлось вариантов» без цифр здесь не появляется.
    """

    if any(violation.severity == Severity.BLOCKING for violation in violations):
        return Outcome.INFEASIBLE
    return Outcome.PLAN_READY
