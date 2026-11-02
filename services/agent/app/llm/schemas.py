"""Схемы вывода шести LLM-слотов и превращение ответа модели в `TripRequest`.

Здесь живёт главное правило provider integration: **модель не арифметикирует и не решает, что значение
достоверно**. Она возвращает поля с цитатой слов пользователя, а код (а) приводит строку к
типу контракта, (б) проверяет, что значение прослеживается в сообщении, (в) сам ставит
`defaulted`/`inferred` — statuses, которые модель выдавать не вправе.

Почему не «модель сразу отдаёт `{"party.adults": 2}`»: такое значение неотличимо от
выдуманного. «вдвоём» → 2 — это вывод из слова, а «2» без слова в сообщении — галлюцинация.
Проверка прослеживаемости (provenance) смотрит ровно на то, что юзер сказал руками: цифры
для чисел, начало слова для названий (_forms_ падежей не совпадают: «из Москвы» ≠ «Москва»).

`FABRICATED_FIELD` — не тихий дефолт: непрослеженное значение отбрасывается, причина
возвращается вызывающему, и тот пишет `known=False` + нарушение.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator

from shared.contracts.enums import (
    BudgetInclude,
    BudgetScope,
    ConstraintKind,
    DirectionMode,
    FieldStatus,
    Theme,
    TransportMode,
)
from shared.contracts.money import Money
from shared.contracts.request import FieldValue, TripRequest

#: Правильные пути — из контракта, а не на память: модель не может выдумать поле, которого
#: нет в `TripRequest`, потому что несуществующий путь отбивается на валидации схемы.
LEGAL_PATHS: frozenset[str] = frozenset(path for path, _ in TripRequest().iter_fields())

Span = str
FieldStatusName = Literal["known", "inferred"]


#: Значимость поля по контракту, а не по впечатлению модели. Понижение hard→soft — это
#: молчаливое ослабление ограничения (инвариант №2): «еду в Калининград» модель отметила как
#: пожелание, и оркестратор имел право искать другие города. Повышать soft→hard тоже нельзя.
_CONTRACT_KINDS: dict[str, ConstraintKind] = {
    path: field.kind for path, field in TripRequest().iter_fields()
    if isinstance(field, FieldValue)
}


def resolve_kind(path: str, claimed: ConstraintKind) -> ConstraintKind:
    """Kind из контракта побеждает kind из вывода модели; несогласие видно в логе."""

    declared = _CONTRACT_KINDS.get(path)
    return declared if declared is not None else claimed


class ParsedDelta(BaseModel):
    """Одно поле, которое модель нашла в сообщении.

    `raw` — кусок слов пользователя (для чисел — с точностью до падежа, для названий — как
    названо). `confidence` модель честно снижает, когда выводит: «в ноябре» без числа дня —
    это уже не со слов, и `inferred` ниже порога означает «надо спросить», а не «подставить».
    """

    path: str
    raw: Span
    kind: ConstraintKind
    status: FieldStatusName = "known"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("path")
    @classmethod
    def _path_exists(cls, value: str) -> str:
        if value not in LEGAL_PATHS:
            raise ValueError(f"unknown field path {value!r}; contract has {sorted(LEGAL_PATHS)[:4]}…")
        return value


class ParseSchema(BaseModel):
    """Слот №1: whole message → список дельт. `note` — для человека, на значимость не влияет."""

    deltas: list[ParsedDelta] = Field(default_factory=list)
    note: str = ""


class QuestionSchema(BaseModel):
    """Слот №2: код уже решил, какое поле добивать; модель даёт только формулировку."""

    text: str
    options: list[str] = Field(default_factory=list)
    allow_skip: bool = True


class FinalistsSchema(BaseModel):
    """Слот №4: три `candidate_id` с причиной. Порядок и score считает код, не модель."""

    picked: list[str] = Field(min_length=1, max_length=3)
    rationale: dict[str, str] = Field(default_factory=dict)


class ExplainSchema(BaseModel):
    """Слот №5: текст карточки. Чисел в схеме нет намеренно — их берёт `Explanation`."""

    text: str


class DiffSchema(BaseModel):
    """Слот №6: сообщение → какие поля контракта задело. Enum-словарь, не свободный вывод."""

    touched: list[str] = Field(default_factory=list)
    unchanged_note: str = ""

    @field_validator("touched")
    @classmethod
    def _touched_are_paths(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in LEGAL_PATHS]
        if unknown:
            raise ValueError(f"unknown field paths: {unknown}")
        return value


class Rejected(BaseModel):
    """Причина, по которой дельта не попала в состояние."""

    path: str
    raw: str
    reason: str


class ParsedMessage(BaseModel):
    """Итог применения дельт: состояние-дельта + чем её нельзя пользоваться.

    `used_fallback` — признак, что живой ответ не прошёл схему и поле разобрал детерминированный
    парсер. Это не «тихий дефолт»: причину видно в `rejected` и в метрике provider integration.
    """

    request: TripRequest
    rejected: list[Rejected] = Field(default_factory=list)
    used_fallback: bool = False
    #: почему фолбэк: "schema_failed" — вывод не прошёл схему, "provider_unavailable" — шлюз
    #: не ответил. Разные причины по-разному чинятся, и в метрике это надо видеть.
    fallback_reason: str = ""
    #: мягкие поля добраны у детерминированного парсера (`top_up_soft_fields`): модель ответила,
    #: но темы/предпочтения не вернула. Это НЕ фолбэк — hard-поля по-прежнему её, и в sweep'е
    #: строка остаётся валидной метрикой; флаг нужен, чтобы «молча неоплаченный слот №3» был
    #: виден числом, а не выводом из пустого `relevance_query`.
    soft_top_up: bool = False


_NUMBERS = re.compile(r"\d[\d \u00a0]*|\d")
_WORD_PREFIX = 4


def _digits_in(raw_value: str, message: str) -> bool:
    """Все цифровые группы значения встречаются в сообщении (порядок свободный, «150к» → 150).

    Однозначная цифра проверяется отдельным числом, а не подстрокой склейки: «1» внутри «150»
    не значит, что человек назвал первое число.
    """

    groups = [group.replace(" ", "").replace(" ", "") for group in _NUMBERS.findall(raw_value)]
    hay_groups = [group.replace(" ", "").replace(" ", "") for group in _NUMBERS.findall(message)]
    if not groups:
        return False
    hay_digits = "".join(hay_groups)
    for group in groups:
        if len(group) == 1:
            #: однозначная цифра обязана стоять в сообщении отдельным числом: «4» внутри
            #: «1504» — совпадение записи, а не названная ночь
            if group not in hay_groups:
                return False
        elif group not in hay_digits:
            return False
    return True


def _word_in(raw_value: str, message: str) -> bool:
    """Начало значения читается в сообщении: «Москва» ищется как «моск» в «из москвы».

    Модель часто цитирует фразу целиком («с собакой», «не больше 4 часов»), поэтому проверяем
    каждое слово цитаты: достаточно одного слова длиной ≥`_WORD_PREFIX`, начало которого
    встречается в сообщении. Склейка пробелов в одну иглу ловила не всё, а почти ничего.
    """

    needle_words = [word for word in re.findall(r"[а-яёa-z]+", raw_value.lower()) if word]
    if not needle_words:
        # только цифры или знаки: это числовое поле, и сюда попадают пути, забытые в
        # `_NUMERIC_PATHS` — пустая подстрока ловила бы что угодно
        return False
    haystack = re.findall(r"[а-яёa-z]{%d,}" % _WORD_PREFIX, message.lower())
    for word in needle_words:
        if len(word) < _WORD_PREFIX:
            if word in message.lower():
                return True
            continue
        if any(item.startswith(word[:_WORD_PREFIX]) for item in haystack):
            return True
    return False


def _number_traceable(raw_value: str, message: str) -> bool:
    """Число прослеживается цифрами ИЛИ словами («на 7 ночей», «вчетвером»).

    Три формы входа: цитата словами («вдвоём»), цитата цифрами («150к»), пересказ цифрой там,
    где юзер сказал словом (сообщение «вчетвером», raw «4»). Первая и третья — разные: во
    второй значение выводим мы, и оно легально только как `inferred`.
    """

    if _digits_in(raw_value, message):
        return True
    if not _NUMBERS.search(raw_value):
        # цитаты-слова: «вдвоём» — проверяем по буквам, цифр в цитате нет
        return _word_in(raw_value, message)
    spoken = _numbers_from_words(message)
    for group in _NUMBERS.findall(raw_value):
        # юзер мог назвать число словом: «вчетвером» → 4, и цифры «4» в сообщении нет
        if int(re.sub(r"[^\d]", "", group) or 0) in spoken:
            return True
    return False


#: Поля, чьё значение — число: их проверяем по цифрам, а не по буквам.
_NUMERIC_PATHS = frozenset({
    "party.adults", "party.children", "nights", "dates.flexibility_days",
    "lodging.stars_min", "lodging.rooms", "lodging.budget_per_night_max",
    "transport.max_hours_direct", "budget.amount",
})


def _day_tokens(message: str) -> set[int]:
    """Числа сообщения как отдельные значения: «150к» не содержит ни дня 1, ни дня 15.

    Проверка по склейке цифр ловила однозначный день подстрокой: ISO-дата `2026-11-01`,
    которую модель выдумала из «в ноябре», считалась названной, потому что «1» стоит внутри
    «150». Живой контур 2026-10-27: нулевая ночёвка ушла в план с `source="user"`.
    """

    return {int(token) for token in re.findall(r"\d+", message)}


def _date_traceable(raw: str, message: str) -> bool:
    r"""Дата прослеживается по дню и месяцу отдельно, а «2026» из ISO-строки — не день.

    Первый вариант брал `\b(\d{1,2})` от строки целиком и на «2026-11-21» получал «20»:
    сообщение с «с 14 по 21» его не содержало, и правильная дата отбраковывалась как выдумка.
    """

    tokens = _day_tokens(message)
    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if iso is not None:
        day, month = int(iso.group(3)), int(iso.group(2))
    else:
        days = [int(item) for item in re.findall(r"\b(\d{1,2})\b", raw) if 1 <= int(item) <= 31]
        if not days:
            return False
        # каждый названный день диапазона обязан прозвучать: «с 1 по 10» → и 1, и 10
        if not all(item in tokens for item in days):
            return False
        day, month = days[0], None
    if day not in tokens:
        return False
    if month is not None:
        # месяц либо цифрой в сообщении, либо словом («ноября» → 11) — иначе это додумка
        return month in tokens or month_of(message) is not None
    return month_of(raw) is not None or month_of(message) is not None


def _traceable(delta: ParsedDelta, message: str) -> bool:
    """Прослеживается ли значение в словах пользователя.

    Числовые поля — по цифрам ИЛИ словами («вчетвером» = 4). Текстовые — по началу слова.
    Даты — по дню и месяцу отдельно; год не проверяем: его модель берёт из подсказки, а не
    выдумывает.
    """

    raw = delta.raw
    if delta.path in _NUMERIC_PATHS:
        return _number_traceable(raw, message)
    if delta.path in {"dates.start", "dates.end"}:
        return _date_traceable(raw, message)
    return _word_in(raw, message)


#: Только полные stems месяцев: «ма» матчится на любую «маму», а это молча сдвигает дату на май.
_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мае": 5, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
#: «с ребёнком» без возраста: дети названы, возраст — нет. Слово ищут оба парсера
#: (детерминированный `MockSlots.parse` и `apply_deltas` над ответом модели), поэтому оно
#: живёт здесь, а не в `slots.py`: два набора слов разъехались бы на первом же «внучата».
CHILD_WORD_RE = re.compile(r"ребён|ребен|дет[еёи]|младен|малыш|груднич")
#: Безопасный возраст для подстановки: с ним ищется семейный номер и детская кровать, а
#: статус остаётся `defaulted` — код обязан показать, что это его предположение.
DEFAULT_CHILD_AGE = 7

#: Русские слова к значениям `Theme` — одна таблица на оба парсера (детерминированный мок offline fixture
#: и `apply_deltas` над ответом модели), как `month_of` для дат. Со своей таблицей в `slots.py`
#: мок терял падежи: «отель у моря» не содержал ни «море», ни «морю», themes оставались
#: неизвестны, запрос слота №3 выходил пустым, и оплаченный `/rerank` не вызывался вовсе.
THEME_WORDS = {
    Theme.BEACH: ("мор", "пляж", "берег", "береж"),
    Theme.NATURE: ("гор", "лес", "природ", "озер"),
    Theme.CITY: ("город", "центр"),
    Theme.CULTURE: ("музе", "культур", "истори", "экскурси"),
    Theme.FOOD: ("гастро", "еда", "ресторан", "кухн"),
}

#: Слово, внутри которого стем «гор» теряет смысл: «город» — это `CITY`, а не природа. Без
#: исключения каждый городской запрос получал вторую тему, и ложная «природа горы тишина»
#: уходила в оплаченный `/rerank` — снято 2026-10-28 на живом ответе слота №1 `raw="город"`.
THEME_STOP_WORDS = {"гор": ("город",)}


def themes_in(text: str) -> list[Theme]:
    """Один матчер тем на оба парсера: таблица без него разъезжается на первом падеже."""

    lowered = text.lower()
    found: list[Theme] = []
    for theme, words in THEME_WORDS.items():
        for word in words:
            haystack = lowered
            for stop in THEME_STOP_WORDS.get(word, ()):
                haystack = haystack.replace(stop, " ")
            if word in haystack:
                found.append(theme)
                break
    return found


def month_positions(text: str) -> list[int]:
    """Месяцы текста по порядку появления: «с 28 октября по 5 ноября» → [10, 11].

    Порядок важен: `_MONTHS` — словарь, и перебор по его ключам достал бы «октябрь» раньше
    «ноября» только потому, что его ключ стоит выше, а не потому, что он в цитате первый.
    Публичная: ответ на вопрос про даты разбирает `orchestration.nodes`, и второй месяц там
    нужен так же, как здесь.
    """

    lowered = text.lower()
    found = [(lowered.find(stem), number) for stem, number in _MONTHS.items()
             if lowered.find(stem) >= 0]
    found.sort()
    return [number for _, number in found]


def day_in_month(year: int, month: int, day: int) -> date:
    """День в месяце: перенос года и сжатие на реальную границу месяца.

    Нужен всем трём разборщикам — моку offline fixture, коэрсеру живой цитаты и узлу ответа в
    `orchestration.nodes`, — иначе «с 28 по 5» дало бы три разных поведения на одном входе.
    «31 февраля» не выдумывается: день прижимается к 28/29, а не роняет ход — ответ
    пользователя не обязан быть календарно грамотным.
    """

    if month > 12:
        year, month = year + 1, month - 12
    elif month < 1:
        year, month = year - 1, month + 12
    return date(year, month, min(day, monthrange(year, month)[1]))


def _year_after(start: date, end: date) -> date:
    """Выезд не бывает раньше заезда: «28.12 - 05.01» — это январь следующего года.

    Дни убывают, а месяцы названы оба — перехода через месяц (`end_month += 1`) тут уже
    не хватает, остаётся перенос года. Равные границы не трогаем: «с 10 по 10» — пустая
    вилка, и различать её должен вызывающий, а не разборщик.
    """

    return day_in_month(end.year + 1, end.month, end.day) if end < start else end


#: Слова, после которых число — длительность или количество, а не день месяца. `ч\b` нужен
#: «3 - 4 ч в дороге», а `час\w*` — «часах»; «число» под них не попадает, и «по 5 числа»
#: остаётся датой.
_UNIT_AFTER_NUMBER = re.compile(
    r"\s*(?:час\w*|ч\b|ноч\w*|минут\w*|звёзд\w*|звезд\w*|дн[ея]\w*|нед[ея]\w*|км\b|тыс\b)"
)


def _unit_after(text: str, position: int) -> re.Match[str] | None:
    """Единица сразу за вторым числом диапазона: «3 - 4 часов» — это не календарь.

    Отдельная проверка, а не часть рамки: «в ноябре 3 - 4 часа в дороге» содержит слово
    месяца, и без неё ноябрьские даты родились бы из длительности перелёта.
    """

    return _UNIT_AFTER_NUMBER.match(text, position)


#: Один конец вилки: `день`, `дд.мм` или `дд.мм.гггг`. Год и месяц необязательны — человек
#: дописывает их к одному краю диапазона или к обоим.
_DAY_RANGE_END = r"(\d{1,2})(?:[./](\d{1,2})(?:[./](\d{4}))?)?"

#: Разделитель вилки: дефис, тире или «по».
_DAY_RANGE_SEP = r"\s*(?:по|[—–-])\s*"

#: «7-11.11», «11.11-17», «с 7 по 11.11»: дата названа у одного конца, у второго — только
#: день. `dotted` требует две даты, `window` — пробелов вокруг разделителя, и такой ответ на
#: вопрос про даты не читался ничем: ход падал на `cannot read a date range`.
_MIXED_DAY_RANGE = re.compile(rf"\b{_DAY_RANGE_END}{_DAY_RANGE_SEP}{_DAY_RANGE_END}\b")

#: «7-11» — вилка без месяцев и без пробелов. Принимаем её, только если ей исчерпывается
#: вся строка: ответ на вопрос про даты выглядит именно так, а «2-3» внутри фразы остаётся
#: длительностью, которую `window` по-прежнему не трогает.
_COMPACT_DAY_RANGE = re.compile(rf"(?:с\s+)?(\d{{1,2}}){_DAY_RANGE_SEP}(\d{{1,2}})")


def _end(day: str, month: str | None, year: str | None) -> tuple[int, int | None, int | None]:
    return int(day), None if month is None else int(month), None if year is None else int(year)


def range_days(text: str, *, year: int, month: int | None = None) -> tuple[date, date] | None:
    """Диапазон дат из одной строки: ISO, `дд.мм`, «с 28 октября по 5 ноября» и короткие
    формы ответа на вопрос («7-11.11», «7-11»).

    Пересечение месяца — не пустой диапазон: если день выезда меньше дня заезда, выезд
    относится к следующему месяцу (а когда названы два месяца — каждый к своему).
    `month` — якорь, когда месяц в строке не звучал вовсе («с 28 по 5» при известном ноябре);
    без якоря возвращаем `None`, и решение переспросить остаётся за вызывающим.
    Промежуток по времени («не больше 3 - 4 часов») — не диапазон дат.
    """

    iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if len(iso) >= 2:
        return date.fromisoformat(iso[0]), date.fromisoformat(iso[1])
    dotted = re.findall(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b", text)
    if len(dotted) >= 2:
        first, last = dotted[0], dotted[-1]
        start_date = day_in_month(int(first[2]) if first[2] else year, int(first[1]), int(first[0]))
        end_date = day_in_month(int(last[2]) if last[2] else year, int(last[1]), int(last[0]))
        return start_date, _year_after(start_date, end_date)
    #: (день, месяц, год) каждого конца; `None` месяц означает «в строке не назван»
    ends: tuple[tuple[int, int | None, int | None], tuple[int, int | None, int | None]] | None = None
    if (mixed := _MIXED_DAY_RANGE.search(text)) is not None and (mixed.group(2) or mixed.group(5)):
        if _unit_after(text, mixed.end()) is None:
            ends = (_end(mixed.group(1), mixed.group(2), mixed.group(3)),
                    _end(mixed.group(4), mixed.group(5), mixed.group(6)))
    elif compact := _COMPACT_DAY_RANGE.fullmatch(text.strip().lower()):
        ends = (_end(compact.group(1), None, None), _end(compact.group(2), None, None))
    else:
        #: разделитель обязателен с обеими сторонами пробелом: «3-4 звезды» и «2-3 часа» не
        #: должны притворяться диапазоном дат, а «с 14 по 21» и «28 октября по 5» — должны
        window = re.search(r"(\d{1,2})(?:\s+[а-яё]+)?\s+(?:по|—|–|-)\s+(\d{1,2})", text)
        if window is not None and _unit_after(text, window.end()) is None:
            ends = (_end(window.group(1), None, None), _end(window.group(2), None, None))
    if ends is None:
        return None
    months = month_positions(text)
    (start_day, start_month_said, start_year_said), (end_day, end_month_said, end_year_said) = ends
    #: месяц либо назван, либо задан якорем: «2 - 3» без контекста — не дата, и придумывать
    #: за пользователя месяц мы не правомочны
    start_month = start_month_said or (months[0] if months else month)
    if start_month is None:
        return None
    end_month = end_month_said or (months[-1] if len(months) >= 2 else start_month)
    #: месяц не назван второй раз, но дни убывают — значит граница в следующем месяце
    if end_month == start_month and end_day < start_day:
        end_month += 1
    start_date = day_in_month(start_year_said or year, start_month, start_day)
    #: `day_in_month` сам переносит год при `месяц > 12` — второй перенос давал «с 30 по 2»
    #: в декабре поездку на 2028 год вместо января 2027-го
    end_date = day_in_month(end_year_said or year, end_month, end_day)
    return start_date, _year_after(start_date, end_date)


def month_of(text: str) -> int | None:
    """Месяц из названия («ноябре») или из цифры `дд.мм`; ищем по переданному тексту."""

    months = month_positions(text)
    if months:
        return months[0]
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\b", text.lower())
    if match is not None:
        return int(match.group(2))
    return None


#: Слова-числительные сортируются по длине: «одиннадцать» содержит «один», и порядок перебора
#: решал бы за нас, а должен решать размер слова.
_NUMBER_WORDS: dict[int, tuple[str, ...]] = {
    1: ("один", "одна", "одно", "одну", "одной"),
    2: ("два", "две", "двое", "вдвоём", "вдвоем", "двоих", "пара"),
    3: ("три", "трое", "втроём", "втроем", "троих"),
    4: ("четыре", "четверо", "вчетвером", "вчетвёртом", "четверых"),
    5: ("пять", "пятеро", "пятерых", "впятером"),
    #: собирательные формы 6–10 живая модель цитирует словами так же охотно, как «впятером»:
    #: на прогоне 52 фраз «впятером» дало `coerce: no number in 'впятером'`, и hard-поле
    #: состава потерялось при верной цитате. Список форм, а не слов-корней: `\b`
    #: требует точного слова, и «пятеро» внутрь «впятером» не матчится.
    6: ("шесть", "шестеро", "шестерых", "вшестером"),
    7: ("семь", "семеро", "семерых", "всемером"),
    8: ("восемь", "восьмеро", "восьмерых", "восмером"),
    9: ("девять", "девятеро", "девятерых", "вдевятером"),
    10: ("десять", "десятеро", "десятерых", "вдесятером"),
    11: ("одиннадцать",), 12: ("двенадцать",), 13: ("тринадцать",),
    14: ("четырнадцать",), 15: ("пятнадцать",),
    20: ("двадцать",), 30: ("тридцать",),
}


#: Слова проверяются по границе: подстрока «один» сидит внутри «одиннадцать», а без `\b`
#: парсер возвращал 1 вместо 11. Кортежи длиной в один элемент пишутся с запятой — `("восемь")`
#: это строка, и обход по ней даёт отдельные буквы, которые матчатся на любом слове.
_WORD_TO_NUMBER: list[tuple[re.Pattern[str], int]] = sorted(
    ((re.compile(rf"\b{re.escape(word)}\b"), number)
     for number, words in _NUMBER_WORDS.items() for word in words),
    key=lambda item: -len(item[0].pattern),
)


def _numbers_from_words(text: str) -> set[int]:
    lowered = text.lower()
    return {number for pattern, number in _WORD_TO_NUMBER if pattern.search(lowered)}


def number_from_words(text: str) -> int | None:
    """Первое названное словом число: «на двоих» → 2. Граница слова обязательна.

    Публичное имя: этим же словарём пользуется детерминированный парсер из `slots.py`, и на
    «пять ночей» оба разборщика обязаны давать пять, а не «живой — 5, мок — ничего».
    """

    lowered = text.lower()
    for pattern, number in _WORD_TO_NUMBER:
        if pattern.search(lowered):
            return number
    return None


def _to_int(raw: str) -> int:
    """Цифра или русское слово-числительное. «на двоих» — это 2, и терять её нельзя.

    Живая модель цитирует словами именно так, как сказал человек; converter, который знает
    только «вдвоём/втроём», отбраковывал правильный ответ с `coerce: no number in 'трое'`.
    """

    lowered = raw.lower()
    from_words = number_from_words(lowered)
    if from_words is not None:
        return from_words
    match = re.search(r"\d+", raw)
    if match is None:
        raise ValueError(f"no number in {raw!r}")
    return int(match.group(0))


def _to_money(raw: str) -> Money:
    digits = re.sub(r"[^\d]", "", raw.replace(" ", ""))
    if not digits:
        raise ValueError(f"no amount in {raw!r}")
    amount = Decimal(digits)
    if re.search(r"[кk]\b|тысяч", raw.lower()):
        amount *= 1000
    elif re.search(r"млн", raw.lower()):
        amount *= 1_000_000
    return Money(amount=amount, currency="RUB")


def _to_date(raw: str, *, year: int, message: str = "", inferred: bool = False,
             latest: bool = False) -> date:
    """Дата из цитаты. `latest` — для выезда: из «с 1 по 10 августа» брать вторую границу.

    Модель отдаёт одну и ту же цитату диапазона на обе даты (диапазон один, и это правильно),
    а без различия start/end обе даты становились 1 августа — поездка нулевой длины.

    Диапазон читает общий `range_days` — тот же, что у мока offline fixture и у узла ответа: на одном
    входе все три обязаны дать одну дату, иначе метрика живого пути сравнивает
    несравниваемое. Граница берётся по позиции, а не `min`/`max`: в «с 28 октября по 5 ноября»
    минимум — 5, и `min` для заезда давал 5 октября, а `max` для выезда — 28 ноября.
    """

    span = range_days(raw, year=year, month=month_of(raw) or month_of(message))
    if span is not None:
        return span[1] if latest else span[0]
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if iso is not None:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    days = [int(item) for item in re.findall(r"\b(\d{1,2})\b", raw) if 1 <= int(item) <= 31]
    month = month_of(raw) or month_of(message)
    if month is None:
        raise ValueError(f"month missing: raw={raw!r} message={message[:60]!r}")
    if not days:
        # «в ноябре» — дня нет, и выдумывать его право только код: поле помечается
        # `inferred` + `needs_confirmation`, ровно как в моке offline fixture. Живой провайдер не имеет
        # права вести себя строже мока: тот же вход обязан давать тот же вопрос пользователю.
        if not inferred:
            raise ValueError(f"day missing in a field claimed as spoken: {raw!r}")
        day_number = 1
    else:
        #: одна граница в цитате — тот же день, что и брался раньше; `latest` влияет только
        #: когда диапазона не было и день задан один
        day_number = days[-1] if latest else days[0]
    guessed = year
    iso_year = re.search(r"\b(20\d{2})\b", raw)
    if iso_year is not None:
        guessed = int(iso_year.group(1))
    #: 31 февраля модель может выдать и здесь — день прижимается к границе месяца, а не роняет ход
    return day_in_month(guessed, month, day_number)


def _to_list_of_int(raw: str) -> list[int]:
    return [int(item) for item in re.findall(r"\d+", raw)]


def _to_bool(raw: str) -> bool:
    return not raw.lower().startswith(("не", "без", "no"))


def _to_themes(raw: str) -> list[Theme]:
    """Русская фраза → значения `Theme`. English-значения enum юзер не произносит.

    Модель отдаёт фрагмент слов («тёплому морю, горы»), и здесь он разбирается тем же
    словарём, что и мок offline fixture, — иначе два парсера расходились бы на одном и том же входе.
    """

    lowered = raw.lower()
    themes = themes_in(raw)
    for name in (item.strip() for item in re.split(r"[,;\s]+", lowered) if item.strip()):
        for theme in Theme:
            if theme.value == name:
                if theme not in themes:
                    themes.append(theme)
                break
    if not themes:
        raise ValueError(f"no known theme in {raw!r}")
    return themes


#: «рублей» → RUB, а не «РУБ» кириллицей: `Money.currency` проверяется `[A-Z]{3}` и на
#: кириллице упало бы уже позже, в арифметике, а `budget.currency` — просто строка, и
#: латиница/кириллица в нём неразличимы глазом.
_CURRENCY_WORDS = {
    "руб": "RUB", "рос": "RUB", "долл": "USD", "евр": "EUR", "тинь": "CNY", "юань": "CNY",
    "лир": "TRY",
}


def _currency(raw: str) -> str:
    lowered = raw.strip().lower()
    for needle, code in _CURRENCY_WORDS.items():
        if lowered.startswith(needle):
            return code
    match = re.fullmatch(r"[a-zа-ё]{3}", lowered)
    if match is None:
        raise ValueError(f"unknown currency in {raw!r}")
    return lowered.upper()


#: Живая модель цитирует направление вместе с предлогом («в сочи», «из казани»), а в контракте
#: `direction.explicit` и `origin` — название города: детерминированный парсер offline fixture даёт их без
#: предлога. Пока живой путь этого не делал, на прогоне 52 фраз терялись поля именно из-за
#: предлога при верной цитате: значение есть, а сравнение с ожидаемым городом не проходит.
#: Снимается только ведущий предлог с пробелом, внутри названия ничего не трогается.
_PLACE_PREFIX = re.compile(r"^(?:в|во|на|к|ко|из|от|до|у|с)\s+", re.IGNORECASE)


def coerce(path: str, raw: str, *, year: int, message: str = "", inferred: bool = False) -> object:
    """Строка юзера → значение типа контракта. Арифметикует код, не модель.

    Даты собираются из двух мест: день — из фрагмента, месяц — из фрагмента, а если его там
    нет, из всего сообщения. «с 14 по 21 ноября» не содержит рядом месяц и число для каждой
    даты отдельно, и требовать это значило бы заставлять модель додумывать.
    """

    if path in {"party.adults", "nights", "dates.flexibility_days", "lodging.rooms",
                "lodging.stars_min"}:
        return _to_int(raw)
    if path == "party.children":
        return _to_list_of_int(raw)
    if path == "party.pets":
        return _to_bool(raw)
    if path in {"transport.prefer_direct", "transport.overnight_ok"}:
        return _to_bool(raw)
    if path == "transport.max_hours_direct":
        match = re.search(r"\d+(?:[.,]\d+)?", raw)
        if match is None:
            raise ValueError(f"no hours in {raw!r}")
        return float(match.group(0).replace(",", "."))
    if path in {"budget.amount", "lodging.budget_per_night_max"}:
        return _to_money(raw)
    if path in {"dates.start", "dates.end"}:
        return _to_date(raw, year=year, message=message, inferred=inferred,
                        latest=path == "dates.end")
    if path == "themes":
        return _to_themes(raw)
    if path == "direction.mode":
        return DirectionMode(raw.strip().lower())
    if path == "budget.currency":
        return _currency(raw)
    if path == "budget.scope":
        lowered = raw.lower()
        for needle, scope in (("на человека", BudgetScope.PER_PERSON), ("каждому", BudgetScope.PER_PERSON),
                              ("всего", BudgetScope.TOTAL), ("общий", BudgetScope.TOTAL)):
            if needle in lowered:
                return scope
        return BudgetScope(raw.strip().lower())
    if path == "transport.modes":
        names: dict[str, TransportMode] = {
            "самол": TransportMode.PLANE, "лет": TransportMode.PLANE,
            "поезд": TransportMode.TRAIN, "ж-д": TransportMode.TRAIN,
            "авто": TransportMode.AUTO, "машин": TransportMode.AUTO,
            "бuss": TransportMode.AUTO,
        }
        found = [mode for needle, mode in names.items() if needle in raw.lower()]
        if not found:
            raise ValueError(f"no known transport mode in {raw!r}")
        return found
    if path == "budget.includes":
        #: отдельное имя: `names` выше уже занят словарём транспорта, и в одной функции
        #: mypy считает второе присваивание переопределением с другим типом значений
        include_words: dict[str, BudgetInclude] = {
            "прожив": BudgetInclude.LODGING, "отел": BudgetInclude.LODGING,
            "перел": BudgetInclude.TRANSPORT, "лет": BudgetInclude.TRANSPORT,
            "питани": BudgetInclude.ACTIVITIES, "экскурс": BudgetInclude.ACTIVITIES,
            "трансфер": BudgetInclude.TRANSFER,
        }
        includes: list[BudgetInclude] = [
            item for needle, item in include_words.items() if needle in raw.lower()
        ]
        if not includes:
            raise ValueError(f"no known budget scope in {raw!r}")
        return includes
    if path in {"origin", "identity.citizenship", "identity.residence", "direction.explicit"}:
        value = raw.strip()
        if path in {"origin", "direction.explicit"}:
            value = _PLACE_PREFIX.sub("", value).strip() or value
        return value
    if path == "direction.excluded":
        return [_PLACE_PREFIX.sub("", item).strip() or item.strip()
                for item in re.split(r"[,;]", raw) if item.strip()]
    raise ValueError(f"no coercer for {path!r}")


def apply_deltas(deltas: list[ParsedDelta], message: str, *, year: int,
                 fallback: TripRequest | None = None,
                 fallback_reason: str = "schema_failed") -> ParsedMessage:
    """Дельты модели → `TripRequest`-дельта: типы проверяются, выдумка отбрасывается.

    Модель вправе отдать только `known` (со слов) и `inferred` (вывод). `defaulted` ставит
    код, когда сам подставляет безопасное значение — здесь его нет и быть не может.

    `fallback` — результат детерминированного парсера, если живой ответ не прошёл схему или
    провайдер вообще не ответил. Он не подмешивается к валидным дельтам: либо модель
    ответила, либо фолбэк, иначе метрика «0 выдуманных значений» стала бы неверифицируемой.
    """

    if fallback is not None:
        return ParsedMessage(
            request=fallback, used_fallback=True, fallback_reason=fallback_reason,
            rejected=[Rejected(path="*", raw="", reason=f"{fallback_reason} → fallback_parser")],
        )
    request = TripRequest()
    rejected: list[Rejected] = []
    for delta in deltas:
        if delta.status == "known" and not _traceable(delta, message):
            rejected.append(Rejected(path=delta.path, raw=delta.raw,
                                     reason="FABRICATED_FIELD: значение не прослеживается в сообщении"))
            continue
        try:
            value = coerce(delta.path, delta.raw, year=year, message=message,
                           inferred=delta.status == "inferred")
        except (ValueError, InvalidOperation) as error:
            rejected.append(Rejected(path=delta.path, raw=delta.raw, reason=f"coerce: {error}"))
            continue
        if delta.path == "party.children" and not value and CHILD_WORD_RE.search(
                f"{delta.raw} {message}".lower()):
            #: «с ребёнком» без возраста: возраст подставляет код, и статус обязан быть
            #: `defaulted`, как у мока offline fixture, — приписать человеку несказанное нельзя. Пустой
            #: список сюда тоже попадает: живой слот №1 отдавал `raw="с ребёнком"` без цифр,
            #: и `[]` с `source="user"` значило бы «пользователь сказал, что детей нет».
            request.party.children = FieldValue[list[int]].defaulted(
                [DEFAULT_CHILD_AGE], kind=ConstraintKind.HARD
            )
            continue
        try:
            _set_field(request, delta, value)
        except (ValidationError, ValueError) as error:
            rejected.append(Rejected(path=delta.path, raw=delta.raw, reason=f"contract: {error}"))
    if not request.direction.explicit.is_unknown and request.direction.mode.is_unknown:
        # названное направление без режима — режим тот же факт: искать нечего
        request.direction.mode = FieldValue[DirectionMode].known(
            DirectionMode.EXPLICIT, kind=ConstraintKind.HARD, source="user"
        )
    if request.direction.mode.is_unknown:
        #: Мок (`MockSlots.parse`) закрыть режим обязан всегда, и живой путь — тоже:
        #: `defaulted(OPEN)` по AGENTS.md («open → ограничения нет вообще, это триггер
        #: INSPIRE»), а не `known(source="user")`, потому что человек режима не называл.
        #: Без этой строки на живом контуре 2026-10-27 unknown-режим забирал второй вопрос
        #: хода («подбери сам» / «конкретная страна»), ответ про даты не применялся,
        #: `dates.start` оставался пустым, и `compose` собирал план без единой ночёвки.
        request.direction.mode = FieldValue[DirectionMode].defaulted(
            DirectionMode.OPEN, kind=ConstraintKind.HARD
        )
    return ParsedMessage(request=request, rejected=rejected)


def soft_fields_missing(request: TripRequest) -> bool:
    """Есть ли у запроса хоть что-то для слота №3.

    Отдельная функция потому, что решение «добирать или нет» принимают два места (провайдер и
    тесты), а цена расхождения — оплаченный `/rerank`, которого в прогоне не было.
    """

    return request.themes.is_unknown and not request.soft_preferences


def top_up_soft_fields(parsed: ParsedMessage, reference: TripRequest) -> ParsedMessage:
    """Мягкие поля берутся у детерминированного парсера, если модель их не вернула.

    Живой слот №1 отвечает за hard-поля, и там право на ошибку ноль: выдуманная дата ломает план.
    `themes` и `soft_preferences` — единственные поля без чисел, и они же — единственный вход
    слота №3 (`nodes.relevance_query`). Снято живым прогоном 2026-10-28: из 9 повторов модель
    отдала темы 2 раза, и в 7 прогонах `/rerank` не вызывался вовсе — не из-за шлюза, а из-за
    пустого запроса. Молча неоплаченный слот хуже оплаченного неудачного: мерить нечем.

    Правила, которые здесь ограничивают подмешивание:

    * мягкое поле добирается только когда своё `unknown`/пустое — вывод модели не перекрывается;
    * hard-поля не читаются и не пишутся вообще, иначе ошибка модели лечилась бы чужим
      разбором;
    * `used_fallback` остаётся `False`: разбор был, просто у него нет права придумывать темы,
      которых в тексте нет — `MockSlots` берёт их теми же `THEME_WORDS` по буквальному вхождению.
    """

    if not soft_fields_missing(parsed.request):
        return parsed
    updates: dict[str, object] = {}
    if parsed.request.themes.is_unknown and not reference.themes.is_unknown:
        updates["themes"] = reference.themes
    if not parsed.request.soft_preferences and reference.soft_preferences:
        updates["soft_preferences"] = list(reference.soft_preferences)
    if not updates:
        return parsed
    request = parsed.request.model_copy(update=updates)
    return parsed.model_copy(update={"request": request, "soft_top_up": True})


def _set_field(request: TripRequest, delta: ParsedDelta, value: object) -> None:
    """Значение кладётся на путь контракта как `FieldValue[T]` с типом из самой модели.

    Аннотация берётся из `__pydantic_fields__` родителя, а не угадывается по значению:
    иначе `list[int]` и `list[str]` слились бы в один тип, и контракт проверил бы нас на
    честное слово.
    """

    container: BaseModel = request
    for part in delta.path.split(".")[:-1]:
        container = getattr(container, part)
    name = delta.path.split(".")[-1]
    annotation = type(container).__pydantic_fields__[name].annotation
    #: pydantic объявляет `.annotation` как `type[Any] | None`, и для mypy «Field(None)(…)» —
    #: вызов None. Здесь это всегда класс `FieldValue[...]`: контракт строится именно им.
    field_type = cast("type[FieldValue[Any]]", annotation)
    kind = resolve_kind(delta.path, delta.kind)
    #: ветвление явное, а не `**kwargs`: у `known` уверенность не проставляется (её нет зачем),
    #: у `inferred` она обязательна и поле помечается на подтверждение. Словарём `object` это
    #: не проверить — mypy бы молчал, а `confidence=None` у known стало бы случайностью.
    if delta.status == "known":
        field: FieldValue[Any] = field_type(value=value, kind=kind,
                                            status=FieldStatus.KNOWN, source="user")
    else:
        field = field_type(value=value, kind=kind, status=FieldStatus.INFERRED, source="model",
                           confidence=delta.confidence, needs_confirmation=True)
    setattr(container, name, field)
