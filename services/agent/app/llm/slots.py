"""Шесть мест, где в контуре работает модель (`docs/LLM-BOUNDARY.md`).

Здесь моки с фиксированными ответами: T-07 подменит реализацию провайдером, сигнатуры
оставятся. Третий слот (мягкая релевантность) в этой таблице существует, но моделью быть
не должен — его считает retrieval/ranking (T-10, П2), поэтому в `LLMSlots` его нет, а
порядок кандидатов в T-05 приходит из источника вместе со `score`.

Число из мока не может быть выдуманным: слоты берут значения из входа (`Candidate.ref`,
строки `BudgetLedger`) и ничего не арифметикируют — инвариант №3.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from services.agent.app.llm.schemas import (
    CHILD_WORD_RE,
    DEFAULT_CHILD_AGE,
    number_from_words,
)
from services.agent.app.orchestration.policy import QuestionSpec
from shared.contracts.enums import (
    BudgetScope,
    ConstraintKind,
    DirectionMode,
    Theme,
)
from shared.contracts.money import BudgetLedger, Money
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import Candidate

_ADULTS_WORDS = {
    "вдвоём": 2, "для двоих": 2, "нас двое": 2, "мы двое": 2,
    "втроём": 3, "для троих": 3, "нас трое": 3,
    "вчетвером": 4, "вчетвёртом": 4, "для четверых": 4, "нас четверо": 4,
    "впятером": 5, "нас пятеро": 5, "для пятерых": 5,
    "вшестером": 6, "нас шестеро": 6, "всмером": 8, "нас восьмеро": 8,
    #: «один» под строкой «одиннадцать», поэтому только с глаголом: мок ищет подстрокой,
    #: и голое «один» вернуло бы 1 взрослых там, где едет одиннадцать человек.
    "еду один": 1, "лечу один": 1, "полечу один": 1, "едем один": 1, "в одиночку": 1,
}
_THEME_WORDS = {
    Theme.BEACH: ("морю", "море", "пляж"),
    Theme.NATURE: ("гор", "лес", "природа"),
    Theme.CITY: ("город",),
    Theme.CULTURE: ("музе", "культур", "истори"),
    Theme.FOOD: ("гастро", "еда", "ресторан"),
}
#: Предложный («в ноябре») и родительный («с 14 по 21 ноября») — оба способа назвать месяц.
_MONTHS = {
    "январе": 1, "феврале": 2, "марте": 3, "апреле": 4, "мае": 5, "июне": 6,
    "июле": 7, "августе": 8, "сентябре": 9, "октябре": 10, "ноябре": 11, "декабре": 12,
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_NOT_A_PLACE = (
    "янв", "фев", "мар", "апр", "мае", "июн", "июл", "авг", "сен", "окт", "ноя", "дек",
    "зим", "весн", "лет", "осен", "отпуск", "командировк", "выходн", "праздник",
    "морю", "море", "горы", "гор", "пляж", "природ", "теплу", "дач", "дом",
    "аэропорт", "вокзал", "отел", "тур", "поездк", "дорог",
    #: способ передвижения и абстрактные «в/на …», которые регулярка иначе выдала бы за город:
    #: «не больше 3 часов в пути», «еду на неделю», «хочу на поезде в казань»
    "поезд", "самол", "машин", "автобус", "паром", "корабл", "пут", "недел",
)
#: Глагол поездки: сам по себе он направлением не является, но без него «в Сочи» в тексте
#: «из Москвы в Сочи» — это просто предлог с городом, а не названное направление.
_TRAVEL_VERB_RE = re.compile(
    r"\b(?:еду|едем|уедем|уезжаю|уезжаем|лечу|летим|улетим|полечу|поеду|поездка|поездки|"
    r"хочу|хотим|мечтаю|собираемся|рвёмся|сорвёмся|срываемся)\b"
)
#: «в/на + слово» — кандидат на направление. Дальше решает словарь стоп-слов: «на выходные»,
#: «в пути», «в октябре» направлениями быть не могут.
_PLACE_RE = re.compile(r"\b(?:в|на)\s+([а-яё][а-яё-]{2,})")
#: «бюджет 90 тысяч», «бюджет 120 000 рублей», «бюджет 150к» — одна сумма в одном месте.
#: Разряды с пробелом требуют хотя бы одной группы (`+`, а не `*`): с `*` шаблон брал из
#: «150000» первые «150», и бюджет в 150 тысяч становился 150 рублями.
_BUDGET_RE = re.compile(
    r"(?:бюджет|уложимся в|потратим)\D{0,12}(\d{1,3}(?:[ \u00a0]\d{3})+|\d{1,7})"
    r"\s*(к\b|тысяч|млн|руб\w*|₽)?"
)
#: Возраст детей могут перечислить: «дети 8 и 12 лет». Без перечисления regex находил только
#: последнее число, и один ребёнок терялся — hard-поле `party.children` стало бы неполным.
_CHILD_AGE_RE = re.compile(r"(\d{1,2}(?:\s*(?:,|и)\s*\d{1,2})*)\s*(?:лет|год\w*)")


def _budget_from(lowered: str) -> Decimal | None:
    """Сумма из фразы о бюджете. Разряды с пробелом и «к»/«тысяч»/«млн» — один механизм.

    Раньше было два независимых шаблона («150к» где угодно и «бюджет 150000»): «бюджет
    120 000 рублей» и «бюджет 90 тысяч» не ловились neither, а «150к» в начале сообщения без
    слова «бюджет» — только первым шаблоном.
    """

    match = _BUDGET_RE.search(lowered)
    if match is not None:
        amount = Decimal(re.sub(r"[^0-9]", "", match.group(1)))
        unit = (match.group(2) or "") + lowered[match.end():match.end() + 8]
        if "млн" in unit:
            return amount * 1_000_000
        if "тысяч" in unit or re.match(r"^[кk](\b|$)", unit):
            return amount * 1000
        return amount
    if match := re.search(r"(\d{1,3})\s*[кk]\b", lowered):
        return Decimal(match.group(1)) * 1000
    if match := re.search(r"(\d{4,7})\s*(?:₽|руб)", lowered):
        return Decimal(match.group(1))
    return None
DemoMonth = date(2026, 11, 1)


def explicit_destination(text: str) -> str | None:
    """Названное юзером направление — его слова, а не наш вывод.

    Порт из `scripts/quiz_demo.py` (фикс `e101456`): без него «еду в сочи» давало
    `mode=defaulted(OPEN)`, город терялся, и весь поиск шёл по открытому направлению —
    `suggest_destinations` звался там, где реестр (`registry.py`, guard `_destination_known`)
    как раз и смотрит на `direction.explicit`.

    Поле хранит строку, поэтому здесь «сочи» не резолвится в код `sochi`: каталога
    город→код в этом контуре нет, выдумывать совпадение опаснее, чем оставить слово как
    названо.

    Разбор в два шага (глагол поездки + первый не-стоп-слово «в/на …»), а не одна регулярка
    «глагол, предлог, слово»: между ними встаёт способ передвижения («хочу на поезде в
    казань»), а «из Нижнего Новгорода в Казань» вообще не содержит глагола.
    """

    lowered = text.lower()
    if _TRAVEL_VERB_RE.search(lowered) is None and "из " not in lowered:
        return None
    for match in _PLACE_RE.finditer(lowered):
        word = match.group(1)
        if not word.startswith(_NOT_A_PLACE):
            return word
    return None


class Explanation(BaseModel):
    """Вход слота №5: текст пишется по числам, которые посчитал код."""

    headline: str
    nights: int
    guests: int
    check_in: date
    check_out: date
    total: Money
    budget: Money
    remaining: Money
    breakdown: dict[str, Money]
    facts: dict[str, str] = Field(default_factory=dict)
    #: Деньги в том виде, в каком их надо написать: «125 200 ₽». Модель копирует эту строку,
    #: а не форматирует `total` сама. Живой прогон 2026-10-27 выдал «125200.00 RUB»: число
    #: верное, guard его пропускает, и читается как выписка из БД — потому что в модели
    #: `Money` сериализуется именно так, а другой подсказки во входе не было.
    displays: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_parts(cls, *, headline: str, nights: int, guests: int,
                   check_in: date, check_out: date, ledger: BudgetLedger,
                   facts: dict[str, str]) -> Explanation:
        breakdown = {kind.value: money for kind, money in ledger.breakdown.items()}
        displays = {
            "total": display(ledger.total),
            "budget": display(ledger.budget),
            "remaining": display(ledger.remaining),
            **{kind: display(money) for kind, money in breakdown.items()},
        }
        return cls(
            headline=headline,
            nights=nights,
            guests=guests,
            check_in=check_in,
            check_out=check_out,
            total=ledger.total,
            budget=ledger.budget,
            remaining=ledger.remaining,
            breakdown=breakdown,
            facts=facts,
            displays=displays,
        )


class LLMSlots(Protocol):
    """Сигнатура, которую T-07 подменит провайдером, не трогая оркестратор."""

    def parse(self, message: str) -> TripRequest: ...

    def formulate(self, spec: QuestionSpec) -> str: ...

    def soft_relevance(self, query: str,
                       documents: dict[str, str]) -> dict[str, float] | None: ...

    def pick_finalists(self, candidates: list[Candidate]) -> list[Candidate]: ...

    def explain(self, explanation: Explanation) -> str: ...

    def diff(self, old_plan: dict[str, str], new_plan: dict[str, str]) -> str: ...


class MockSlots:
    """Фиксированные ответы; воспроизводимы и без сети (гейт T-05)."""

    def parse(self, message: str) -> TripRequest:
        """Слот №1: сообщение → дельта `TripRequest`.

        Перенесён из `scripts/quiz_demo.py` и дополнен разбором вилки дат («с 14 по 21»),
        чтобы hard-поле приходило со `source="user"`, а не выводом. Мок не приписывает
        пользователю то, чего он не сказал: несказанное остаётся `unknown`, безопасная
        подстановка — `defaulted`.
        """

        request = TripRequest()
        lowered = message.lower()

        destination = explicit_destination(lowered)
        if destination is not None:
            request.direction.mode = FieldValue[DirectionMode].known(
                DirectionMode.EXPLICIT, kind=ConstraintKind.HARD
            )
            request.direction.explicit = FieldValue[str].known(
                destination, kind=ConstraintKind.HARD, source="user"
            )
        else:
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
        #: «дети 4 и 6 лет» — `дет[её]` эту форму не ловило, и перечисленные дети терялись
        child = CHILD_WORD_RE.search(lowered)
        if child is not None:
            # возраст ищем по «3 года / 7 лет»: старый шаблон терял число из-за пробела после
            # «ребёнком» и молча подставлял 7 — то есть выдумывал hard-поле со `source="user"`.
            ages = [int(age) for series in _CHILD_AGE_RE.findall(lowered)
                    for age in re.findall(r"\d{1,2}", series)]
            if ages:
                request.party.children = FieldValue[list[int]].known(
                    ages, kind=ConstraintKind.HARD, source="user"
                )
            else:
                #: «с ребёнком» без возраста: сам факт назван, возраст — нет. Плейсхолдер 7
                #: остаётся (иначе count_guests посчитает семью без детей), но `defaulted`
                #: вместо `known(source="user")`: приписать человеку несказанное — ровно то,
                #: против чего guard T-07 и создан.
                request.party.children = FieldValue[list[int]].defaulted(
                    [DEFAULT_CHILD_AGE], kind=ConstraintKind.HARD
                )
        #: «с собакой», «собака летит с нами», «с животным», «кошка» — и отрицание «без питомца»
        if re.search(r"\b(?:собак\w*|кошк\w*|кот|кота|котёнка|котенка|питомец|питомца|"
                     r"животн\w*)\b", lowered) and not re.search(r"без (?:питомц|животн|собак|кошк)", lowered):
            request.party.pets = FieldValue[bool].known(True, kind=ConstraintKind.HARD, source="user")

        origin = re.search(r"из ([а-яё]+)", lowered)
        if origin is not None:
            city = origin.group(1).capitalize()
            request.origin = FieldValue[str].known(city, kind=ConstraintKind.HARD, source="user")

        budget = _budget_from(lowered)
        if budget is not None:
            request.budget.amount = FieldValue[Money].known(
                Money(amount=budget, currency="RUB"), kind=ConstraintKind.HARD, source="user"
            )
        #: scope — hard-поле: от него зависит, умножать ли бюджет на число людей.
        if re.search(r"на (?:человек|каждого|персону)|каждому", lowered):
            request.budget.scope = FieldValue[BudgetScope].known(
                BudgetScope.PER_PERSON, kind=ConstraintKind.HARD, source="user"
            )
        elif re.search(r"на (?:всех|двоих|троих)|общий", lowered):
            request.budget.scope = FieldValue[BudgetScope].known(
                BudgetScope.TOTAL, kind=ConstraintKind.HARD, source="user"
            )
        if match := re.search(r"не больше (\d{1,2}(?:[.,]\d)?)\s*(?:ч\b|час)", lowered):
            # `(?:час|ч)\b` из старой версии не матчит ни «часов», ни «ч в дороге»: первый
            # вариант съедал «час» и падал на `\b`, второй требовал границы после «ч».
            request.transport.max_hours_direct = FieldValue[float].known(
                float(match.group(1).replace(",", ".")), kind=ConstraintKind.HARD, source="user"
            )
        if "прям" in lowered and ("рейс" in lowered or "перел" in lowered):
            request.transport.prefer_direct = FieldValue[bool].known(
                True, kind=ConstraintKind.SOFT, source="user"
            )

        month = next((number for word, number in _MONTHS.items() if word in lowered), None)
        if month is not None:
            anchor = DemoMonth.replace(month=month, day=1)
            window = re.search(r"с (\d{1,2}) по (\d{1,2})", lowered)
            if window is not None:
                start = anchor.replace(day=int(window.group(1)))
                end = anchor.replace(day=int(window.group(2)))
                request.dates.start = FieldValue[date].known(start, kind=ConstraintKind.HARD, source="user")
                request.dates.end = FieldValue[date].known(end, kind=ConstraintKind.HARD, source="user")
                request.nights = FieldValue[int].inferred(
                    (end - start).days, kind=ConstraintKind.HARD, source="dates", confidence=1.0
                )
            else:
                # «в ноябре» — это не дата: вывели мы, значит поле требует подтверждения
                request.dates.start = FieldValue[date].inferred(
                    anchor, kind=ConstraintKind.HARD, source="context", confidence=0.5
                )
            request.dates.flexibility_days = FieldValue[int].defaulted(0, kind=ConstraintKind.SOFT)

        if match := re.search(r"(\d{1,2})\s*(?:ноч|ночей)", lowered):
            request.nights = FieldValue[int].known(int(match.group(1)), kind=ConstraintKind.HARD, source="user")
        elif match := re.search(r"([а-яё]+)\s*(?:ноч(?:и|ей)?|ночь)", lowered):
            #: «пять ночей» числами не ловится, а nights — hard-поле: без этой ветки мок
            #: терял названную длительность. Словарь числительных тот же, что у живого
            #: парсера (`schemas._NUMBER_WORDS`), иначе два разборщика разошлись бы на одном входе.
            spelled = number_from_words(match.group(1))
            if spelled is not None:
                request.nights = FieldValue[int].known(spelled, kind=ConstraintKind.HARD, source="user")

        return request

    def formulate(self, spec: QuestionSpec) -> str:
        """Слот №2: код решил, какой путь поля добить — модель даёт формулировку."""

        if spec.evidence:
            details = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in spec.evidence.items())
            return (
                f"{spec.why} Сейчас так: {details}. "
                f"Как поступить — {', '.join(f'«{option}»' for option in spec.options)}?"
            )
        if spec.options:
            return f"{spec.why} Варианты: {', '.join(spec.options)}."
        return spec.why

    def soft_relevance(self, query: str,
                       documents: dict[str, str]) -> dict[str, float] | None:
        """Слот №3 офлайн: `None` — порядок остаётся тем, как его отдал источник.

        Мок не умеет скорить текст, и выдумывать баллы «по названию» — это подмешать в
        воспроизводимый прогон число, которого никто не считал. Честный офлайн-ответ: `None`,
        и `compose` выбирает отель по цене, как до врезки слота №3
        (CONTRACTS.md, «Мягкая релевантность (слот №3)»).
        """

        return None

    def pick_finalists(self, candidates: list[Candidate]) -> list[Candidate]:
        """Слот №4: из отфильтрованного кодом набора — три с причиной.

        Порядок задан источником (`score`), мок ничего не пересчитывает и не добавляет
        чисел: причина цитирует `ref` того же кандидата.
        """

        ordered = sorted(candidates, key=lambda item: -(item.score if item.score is not None else 0.0))
        finalists: list[Candidate] = []
        for candidate in ordered[:3]:
            name = candidate.ref.get("name", candidate.candidate_id)
            reason = f"{name} — тёплое море и короткий перелёт"
            finalists.append(candidate.model_copy(update={"ref": {**candidate.ref, "why": reason}}))
        return finalists

    def explain(self, explanation: Explanation) -> str:
        """Слот №5: текст по структурированным данным; числа — только из входа.

        Мок не называет даже те числа, которые посчитал код, но не положил в вход
        (например, состав гостей): текст собирается из `Explanation`, а `Explanation`
        собирается из `TripState` и `BudgetLedger` — иначе антигаллюцинационный guard
        нечего было бы проверять.
        """

        parts = [
            f"{explanation.headline}",
            f"Заезд {explanation.check_in:%d.%m}, выезд {explanation.check_out:%d.%m} — "
            f"{explanation.nights} ночей.",
        ]
        if explanation.facts.get("sea_temp_c"):
            parts.append(f"Море +{explanation.facts['sea_temp_c']} °C, "
                         f"перелёт {explanation.facts['flight_h']} ч в одну сторону.")
        parts.append(
            f"Итого {display(explanation.total)} из {display(explanation.budget)}, "
            f"остаток {display(explanation.remaining)}."
        )
        parts.append(_money_line(explanation))
        return " ".join(parts)

    def diff(self, old_plan: dict[str, str], new_plan: dict[str, str]) -> str:
        """Слот №6: что изменилось при пересборке (T-08 подключит на своих входах)."""

        changed = [
            f"{component}: {old_plan[component]} → {new_plan[component]}"
            for component in new_plan
            if component in old_plan and old_plan[component] != new_plan[component]
        ]
        kept = [component for component in new_plan if old_plan.get(component) == new_plan[component]]
        if not changed:
            return f"Ничего не подменяли: {', '.join(kept)}."
        return f"Изменилось: {'; '.join(changed)}. Осталось прежним: {', '.join(kept)}."


def display(money: Money) -> str:
    """«148 900 ₽» — разделение разрядов, сумма берётся готовой из `BudgetLedger`."""

    quantized = money.rounded().amount
    digits = f"{int(quantized):,}".replace(",", " ")
    return f"{digits} ₽"


def _money_line(explanation: Explanation) -> str:
    order = ("transport", "lodging", "transfer", "activity", "fee")
    named = {"transport": "перелёт", "lodging": "проживание", "transfer": "трансфер",
             "activity": "активности", "fee": "сборы"}
    pieces = [
        f"{named[kind]} {display(explanation.breakdown[kind])}"
        for kind in order
        if kind in explanation.breakdown
    ]
    return "Разбивка: " + ", ".join(pieces) + "."
