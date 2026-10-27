"""Узлы контура: чистые функции `(state, вход) → state`.

Правила, которые здесь важны (INFA.md, ARCHITECTURE.md §8):

* каждый узел отдаёт **копию** через `next_version()` — предыдущие версии не мутируются;
* внутри узлов нет I/O, сети, `input()` и `datetime.now()`: время и ответы источников
  приходят аргументами;
* счётчики хода (`budgets`) ведёт рантайм, узел меняет только решения;
* ни один узел не ослабляет hard-ограничение: если совместимой комбинации нет, узел отдаёт
  blocking-нарушение с посчитанными числами, а не «почти подходящий» вариант (инвариант №2).

Типизированные входы (`DestinationFact`, `HotelOffer`, …) — то, что отдаёт инструмент-заглушка;
в T-10 их заменит retrieval поверх БД, сигнатура узла останется.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from services.agent.app.llm.schemas import range_days
from services.agent.app.orchestration.policy import QuestionSpec
from services.agent.app.validation.coherence import make
from services.agent.app.validation.inputs import (
    HotelSpecs,
    Plan,
    StayNight,
    TransferLeg,
    TransportLeg,
)
from shared.contracts.enums import (
    BudgetInclude,
    BudgetScope,
    ConstraintKind,
    DataStatus,
    DirectionMode,
    FieldStatus,
    Outcome,
    PriceKind,
    RunStatus,
    Stage,
    ViolationCode,
)
from shared.contracts.money import Money, PriceLine
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import Candidate, TripState
from shared.contracts.violation import Violation

logger = logging.getLogger(__name__)

SECTIONS: tuple[str, ...] = ("direction", "dates", "party", "budget", "transport", "lodging", "identity")
LEAVES: tuple[str, ...] = ("origin", "nights", "themes")
#: Что код подставляет молча, когда пользователь этого не сказал (CONTRACTS.md, таблица
#: «Политика hard / soft»): статус `defaulted` — подстановка видна в UI как предположение.
#: В сумму бюджета по умолчанию попадает всё, что мы считаем: недооценить дешевле, чем
#: переоценить.
SAFE_INCLUDES: list[BudgetInclude] = [BudgetInclude.TRANSPORT, BudgetInclude.LODGING, BudgetInclude.TRANSFER]


# ---------------------------------------------------------------- входы узлов


class DestinationFact(BaseModel):
    """Строка источника про направление: числа идут данными, а не текстом.

    `sea_temp_c` и `est_total` — `None` там, где источник не дал числа: в реальном каталоге
    `data/seeds` морской температуры нет ни у одного из 61 направлений, и подставлять вместо
    неё выдумку (или ноль) запрещено контрактом «Климат: месяц поездки и отсутствующее число».
    """

    code: str
    name: str
    country: str
    sea_temp_c: Decimal | None
    flight_h: str
    est_total: Money | None
    source_id: str
    score: float
    data_status: DataStatus = DataStatus.SYNTHETIC


class ClimateFact(BaseModel):
    """Погода направления за конкретный месяц.

    `month` — обязательная часть ключа: сида климат ключуется парой `(dest_code, month)`, и
    карточка октября не имеет права показывать ноябрьское число. Отсутствующее число
    (`sea_temp_c=None`) — это статус `unknown`, а не «холодно» и не ноль.
    """

    code: str
    month: str
    sea_temp_c: Decimal | None
    day_temp_c: Decimal | None
    source_id: str
    data_status: DataStatus = DataStatus.SYNTHETIC


class HotelOffer(BaseModel):
    destination_code: str
    name: str
    stars: int
    specs: HotelSpecs
    stays: list[StayNight] = Field(default_factory=list)
    score: float
    #: удобства приходят из источника (`hotels.amenities` в схеме сида) и нужны ровно одному
    #: месту — индексируемому тексту слота №3. Без них «атмосферный» неотличим от «не
    #: атмосферного»: в тексте остаются только название и звёзды, и cross-encoder скорит
    #: вывеску. Пустой список — легальная выдача источника, а не ошибка.
    amenities: list[str] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.SYNTHETIC


class TransportOffer(BaseModel):
    destination_code: str
    arrival: TransportLeg        # туда: прилёт важен для стыковки с check-in
    departure: TransportLeg      # обратно: от него зависит день выезда из номера
    data_status: DataStatus = DataStatus.SYNTHETIC

    @property
    def hours(self) -> float:
        return max(self.arrival.duration_h, self.departure.duration_h)

    @property
    def price_total(self) -> Decimal:
        prices = [leg.price for leg in (self.arrival, self.departure) if leg.price is not None]
        return sum((price.amount.amount for price in prices), Decimal(0))


class TransferOffer(BaseModel):
    destination_code: str
    arrival: TransferLeg
    departure: TransferLeg
    #: к какому отелю относится трансфер: в `data/seeds/transfers.json` одна строка на отель
    #: (`transfer_id = "trf-<hotel_id>"`), и брать первую из списка для выбранного отеля —
    #: значит пришить гостинице чужой путь от аэропорта. Пусто, когда источник не связывает
    #: строки (офлайн-фикстура золотого пути): там сохраняется прежний выбор по порядку.
    hotel_id: str = ""
    data_status: DataStatus = DataStatus.SYNTHETIC


class SearchResult(BaseModel):
    """Ответ инструмента: один тип на все вызовы, незаполненные срезы остаются пустыми.

    Тип нужен ещё и для того, чтобы черновик хода переживал `model_dump_json()` — результат
    источника, пришедший до вопроса, не теряется при рестарте процесса.
    """

    destinations: list[DestinationFact] = Field(default_factory=list)
    climate: list[ClimateFact] = Field(default_factory=list)
    transport: list[TransportOffer] = Field(default_factory=list)
    hotels: list[HotelOffer] = Field(default_factory=list)
    transfers: list[TransferOffer] = Field(default_factory=list)
    order_id: str = ""
    #: счётчики самого источника: сколько строк каталога он рассмотрел и сколько отсек по
    #: каждой причине. Пустая выдача без них — это «финалистов нет» без объяснения, а отказ
    #: по контракту обязан быть с числами (CONTRACTS.md, «Названное направление», исход 3).
    meta: dict[str, str] = Field(default_factory=dict)


def merge_results(results: Sequence[SearchResult]) -> SearchResult:
    """Все ответы источника хода — в один вход сборки; порядок вызовов сохранён."""

    merged = SearchResult()
    for result in results:
        merged.destinations.extend(result.destinations)
        merged.climate.extend(result.climate)
        merged.transport.extend(result.transport)
        merged.hotels.extend(result.hotels)
        merged.transfers.extend(result.transfers)
        merged.meta.update(result.meta)
    return merged


class Composition(BaseModel):
    """Итог сборки: либо совместимый план со строками бюджета, либо причина отказа.

    Модель, а не dataclass: черновик сборки переживает сериализацию прерванного хода
    (`runtime.InterruptedRun`), иначе рестарт процесса после вопроса про hard-нарушение
    потерял бы собранный план и пересобирать его было бы не из чего.
    """

    plan: Plan | None = None
    lines: list[PriceLine] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)
    blocking: list[Violation] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)


# ------------------------------------------------------------------- узлы


def apply_delta(state: TripState, delta: TripRequest) -> TripState:
    """PARSE: сообщение породило дельту требований; история чата состоянием не становится.

    `stage = PARSE` — завершённая фаза, а не та, что начинается: `policy.next_stage` трактует
    поле как «что уже отработало» и по нему выбирает следующее ребро.
    """

    merged, conflicts = merge_request(state.request, delta)
    next_state = state.next_version()
    next_state.request = derive_defaults(merged)
    next_state.violations = list(conflicts)
    next_state.stage = Stage.PARSE
    next_state.awaiting_fields = []
    next_state.outcome = None
    next_state.run_status = RunStatus.RUNNING
    return next_state


def apply_answer(state: TripState, path: str, raw: str) -> TripState:
    """Ответ на вопрос: поле получает `source="user"` (или `default` при пропуске).

    Идемпотентность по `(run_id, field)` обеспечивает рантайм; здесь только изменение поля.
    """

    request = state.request.model_copy(deep=True)
    if path == "origin":
        _require_not_skipped(path, raw)
        request.origin = FieldValue[str].known(raw.strip(), kind=ConstraintKind.HARD)
    elif path == "dates.start":
        _apply_dates(request, raw)
    elif path == "budget.amount":
        amount = _money(raw)
        if amount is None:
            raise ValueError(f"cannot read a budget from {raw!r}")
        request.budget.amount = FieldValue[Money].known(amount, kind=ConstraintKind.HARD)
    elif path == "transport.max_hours_direct":
        hours = _hours(raw)
        if hours is None:
            raise ValueError(f"cannot read travel hours from {raw!r}")
        request.transport.max_hours_direct = FieldValue[float].known(hours, kind=ConstraintKind.HARD)
    elif path == "direction.mode":
        open_choice = raw.strip().lower() in {"подбери сам", "открыто", "open", ""}
        request.direction.mode = FieldValue[DirectionMode].known(
            DirectionMode.OPEN if open_choice else DirectionMode.EXPLICIT, kind=ConstraintKind.HARD
        )
    elif path == "direction.explicit":
        # CONTRACTS.md, «Названное направление»: ответ — либо новое название, либо «подбери
        # сам». Третьего нет, и прежний план с прежним нарушением здесь не остаётся: узел
        # снимает поле, а следующий прогон переспрашивает подборщик по новому значению.
        answer = raw.strip()
        if answer.lower() in {"подбери сам", "открыто", "open", "любая", "не важно", ""}:
            request.direction.explicit = FieldValue[str].unknown(kind=ConstraintKind.HARD)
            request.direction.mode = FieldValue[DirectionMode].known(
                DirectionMode.OPEN, kind=ConstraintKind.HARD
            )
        else:
            request.direction.explicit = FieldValue[str].known(
                answer, kind=ConstraintKind.HARD, source="user"
            )
            request.direction.mode = FieldValue[DirectionMode].known(
                DirectionMode.EXPLICIT, kind=ConstraintKind.HARD
            )
    else:
        raise ValueError(f"no such question: {path}")

    next_state = state.next_version()
    next_state.request = derive_defaults(request)
    next_state.awaiting_fields = [field_path for field_path in state.awaiting_fields if field_path != path]
    next_state.outcome = None
    next_state.stage = Stage.PARSE
    next_state.run_status = RunStatus.RUNNING
    logger.info("answer applied trip=%s field=%s", state.trip_id, path)
    return next_state


def ask(state: TripState, spec: QuestionSpec) -> TripState:
    """CLARIFY = interrupt: пауза на ответе пользователя уже в границе хода (§2).

    Один вопрос на паузу: `awaiting_fields` — ровно это поле, а счётчик вопросов растёт на
    один. Два вопроса «в полёте» разрешили бы ход, который не укладывается в лимит.
    """

    next_state = state.next_version()
    next_state.stage = Stage.CLARIFY
    next_state.awaiting_fields = [spec.field]
    next_state.questions_asked = state.questions_asked + 1
    next_state.run_status = RunStatus.INTERRUPTED
    next_state.outcome = Outcome.AWAITING_ANSWER
    return next_state


def absorb(state: TripState, tool: str, result: SearchResult) -> TripState:
    """SEARCH/INSPIRE: ответ источника ложится в `candidates` и `provenance`.

    Числа карточки (температура моря, часы в дороге, цена «от») — строки источника в `ref`:
    модель их не считает и переиспользовать их может только код.
    """

    next_state = state.next_version()
    candidates = next_state.candidates
    if tool == "suggest_destinations":
        candidates.destinations = [_destination_candidate(fact) for fact in result.destinations]
        next_state.provenance["destinations"] = _status(result.destinations)
    elif tool == "weather_climate":
        facts = {fact.code: fact for fact in result.climate}
        candidates.destinations = [
            candidate.model_copy(update={"ref": {
                **candidate.ref,
                # отсутствие числа — пустая строка, а не «None»: карточка по такому ключу
                # промолчит про море, а `unverified_numbers` не найдёт в тексте выдумку
                "sea_temp_c": _shown(facts[candidate.candidate_id].sea_temp_c),
                "day_temp_c": _shown(facts[candidate.candidate_id].day_temp_c),
                "climate_month": facts[candidate.candidate_id].month,
            }})
            if candidate.candidate_id in facts else candidate
            for candidate in candidates.destinations
        ]
        next_state.provenance["climate"] = _status(result.climate)
    elif tool == "transport_search":
        candidates.transport = [
            Candidate(
                candidate_id=leg.leg_id,
                kind="transport",
                ref={
                    "destination": offer.destination_code,
                    "duration_h": f"{leg.duration_h}",
                    "direct": "true" if leg.direct else "false",
                    "component": leg.component,
                    "arr_at": leg.arr_at.isoformat(),
                    "dep_at": leg.dep_at.isoformat(),
                    "overnight": "true" if leg.overnight else "false",
                    "price": "" if leg.price is None else leg.price.line_id,
                    "source_id": leg.price.source_id if leg.price is not None else "",
                },
                score=-leg.duration_h,
                data_status=offer.data_status,
            )
            for offer in result.transport
            for leg in (offer.arrival, offer.departure)
        ]
        next_state.provenance["transport"] = _status(result.transport)
    elif tool == "hotel_search":
        candidates.hotels = [
            Candidate(
                candidate_id=offer.specs.hotel_id,
                kind="hotel",
                ref={
                    "destination": offer.destination_code,
                    "name": offer.name,
                    "stars": str(offer.stars),
                    "capacity": "" if offer.specs.capacity is None else str(offer.specs.capacity),
                    "pet_friendly": "true" if offer.specs.pet_friendly else "false",
                    "check_in_deadline": offer.specs.check_in_deadline,
                    "source_id": offer.specs.source_id or "",
                    # текст слота №3: строки источника, чисел здесь нет
                    "amenities": ", ".join(offer.amenities),
                },
                score=offer.score,
                data_status=offer.data_status,
            )
            for offer in result.hotels
        ]
        next_state.provenance["hotels"] = _status(result.hotels)
    elif tool == "transfer_search":
        candidates.transfers = [
            Candidate(
                candidate_id=transfer.transfer_id,
                kind="transfer",
                ref={
                    "destination": offer.destination_code,
                    "kind": transfer.kind,
                    "minutes": str(transfer.minutes),
                    "source_id": transfer.price.source_id if transfer.price is not None else "",
                },
                score=1.0,
                data_status=offer.data_status,
            )
            for offer in result.transfers
            for transfer in (offer.arrival, offer.departure)
        ]
        next_state.provenance["transfers"] = _status(result.transfers)
    else:
        raise ValueError(f"tool result cannot be absorbed into candidates: {tool}")

    return next_state


#: `Theme` — английские slug-значения, а реранкер скорит русский текст кандидата. Без
#: перевода запрос «море, атмосферно» выглядел бы как «beach, атмосферно», и сходство по
#: теме терялось бы на уровне лексики, а не смысла.
_THEME_RU: dict[str, str] = {
    "beach": "море пляж",
    "city": "город прогулки экскурсии",
    "nature": "природа горы тишина",
    "culture": "культура история музеи",
    "food": "еда рестораны кухня",
}

#: Шаг округления мягкой релевантности (CONTRACTS.md, «Мягкая релевантность (слот №3)»).
#: Ниже шага выбор менять нельзя: cross-encoder различает 0.83 и 0.85, а человек между этими
#: отелями — нет, и порядок кандидатов начал бы переворачиваться на шуме модели.
RELEVANCE_STEP = 0.05

#: Границы нормализации балла в пределах одного вызова и нейтральное значение, когда
#: различить нечего. 0.05 как нижняя граница — не «самый плохой отель», а гарантия, что
#: оценённый кандидат остаётся выше неоценённого (у того нет `relevance`, и ключ за него
#: берёт 0.0).
RELEVANCE_FLOOR = 0.05
RELEVANCE_TOP = 1.00
RELEVANCE_NEUTRAL = 0.50


def relevance_query(request: TripRequest) -> str:
    """Запрос слота №3: `" ".join(soft values)` (ARCHITECTURE.md §5), темы — переводом."""

    themes = " ".join(_THEME_RU.get(theme.value, theme.value)
                      for theme in (request.themes.value or []))
    return " ".join(part for part in (request.pref_text, themes) if part).strip()


#: Удобства источника (`hotels.amenities` в схеме сида) — английские slug-и, а запрос слота №3
#: русский. Переводит код по словарю сида, а не модель: список закрытый (12 значений), и
#: придумывать формулировку cross-encoder'у незачем.
_AMENITY_RU: dict[str, str] = {
    "wifi": "wi-fi",
    "breakfast": "завтрак включён",
    "pool": "бассейн",
    "parking": "парковка",
    "spa": "спа",
    "gym": "фитнес-центр",
    "beach_first_line": "первая линия пляжа",
    "airport_shuttle": "трансфер из аэропорта",
    "kids_club": "детский клуб",
    "restaurant": "ресторан на территории",
    "laundry": "стирка",
    "air_conditioning": "кондиционер",
}


def hotel_document(candidate: Candidate) -> str:
    """Индексируемый текст отеля: вывеска, звёзды, направление, удобства источника."""

    ref = candidate.ref
    amenities = ", ".join(
        _AMENITY_RU.get(item.strip(), item.strip())
        for item in ref.get("amenities", "").split(",") if item.strip()
    )
    parts = [
        ref.get("name", candidate.candidate_id),
        f"{ref['stars']} звёзд" if ref.get("stars") else "",
        ref.get("destination", ""),
        amenities,
    ]
    return ", ".join(part for part in parts if part)


def apply_relevance(state: TripState, scores: Mapping[str, float]) -> TripState:
    """SEARCH (слот №3): мягкая релевантность ложится в `ref`, `score` источника не тронут.

    Два числа в состоянии — не дублирование: по `score` решает источник, по `relevance` —
    мягкое предпочтение, и в трейсе видно, какой именно отель поднялся из-за слота №3.
    """

    next_state = state.next_version()
    buckets = _relevance_buckets(scores)
    next_state.candidates.hotels = [
        candidate.model_copy(update={
            "ref": {**candidate.ref, "relevance": buckets[candidate.candidate_id]}})
        if candidate.candidate_id in buckets else candidate
        for candidate in next_state.candidates.hotels
    ]
    return next_state


def _relevance_buckets(scores: Mapping[str, float]) -> dict[str, str]:
    """Скоры одного вызова реранкера → баллы `ref["relevance"]` (CONTRACTS.md, слот №3).

    Нормализация по минимуму/максимуму вызова, а не по сырому числу: шкала у
    cross-encoder'ов своя. Измерено 2026-10-27 живым прогоном — `qwen3-reranker-8b` на
    русских документах отдаёт 0.0013–0.0192, `rerank-3-lite` — 0.30–0.49. Без приведения
    весь набор ложился в bucket'ы «0.00»/«0.05», и оплаченный слот не менял план.

    Порядок сохраняется (отображение монотонное), зато различие становится видимым по шагу.
    Нижняя граница — 0.05, не 0.00: у неоценённого кандидата балла нет и `_hotel_order`
    читает за него 0.0, поэтому оценённый обязан остаться выше.
    """

    if not scores:
        return {}
    values = list(scores.values())
    low, high = min(values), max(values)
    span = high - low

    def scaled(score: float) -> float:
        #: все скоры равны — сигнала нет, нейтральный балл, и решает стоимость ночи
        if span <= 0:
            return RELEVANCE_NEUTRAL
        ratio = (score - low) / span
        return RELEVANCE_FLOOR + ratio * (RELEVANCE_TOP - RELEVANCE_FLOOR)

    return {candidate_id: _bucketed(scaled(score)) for candidate_id, score in scores.items()}


def _bucketed(value: float) -> str:
    """Нормализованный балл, округлённый до шага 0.05, строкой: 0.367 → «0.35».

    Хранится строкой, потому что `ref` — `dict[str, str]` (контраст с числами источника
    ровно там же, где они лежат), и `_hotel_order` читает это число обратно.
    """

    steps = round(value / RELEVANCE_STEP)
    return f"{steps * RELEVANCE_STEP:.2f}"


#: Число с единицей: «150 000 ₽», «9 ч», «+29°», «7 ночей», «150к». Вырезается целиком,
#: иначе после удаления цифры в строке висело бы «+ °» или «ночей». Однобуквенные единицы
#: (`к`, `ч`, `м`, `C`) требуют непустого продолжения: без `(?![а-яё])` «3 комнаты»
#: превратилось бы в «омнаты».
_MEASURE_RE = re.compile(
    r"\d[\d \u00a0]*(?:[.,]\d+)?(?:\s*(?:₽|руб\.?|млн\.?|тысяч|°C|°|[MCчк](?![а-яё])|"
    r"час[а-яё]*|мин[а-яё]*|км(?![а-яё])|дн[её]?й?|ноч[её]й?|зв[её]зд[а-яё]*|"
    r"см(?![а-яё])))?",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(r"\s*[+\-–—<>=/и]+\s*(?=[,.;]|\Z)")


def _known_numbers(known: Mapping[str, str]) -> str:
    """Значения `ref` одним куском через `;`: сравнение подстрокой, как в guard'е.

    Разделитель обязателен: без него «24» и «6.7» склеились бы в «246.7», и число «46»
    прошло бы проверку по признаку, которого у кандидата нет.
    """

    joined = ";".join(str(value) for value in known.values())
    return joined.replace(" ", "").replace("\u00a0", "")


def _without_invented_numbers(text: str, known: Mapping[str, str]) -> str:
    """Разрешаем только те числа, которые уже лежат в `ref` этого же кандидата.

    Сравнение — подстрокой по нормализованному числу, то есть ровно тем же правилом, что у
    `unverified_numbers` (runtime): два счётчика честности не должны расходиться. Прежнее
    множество групп цифр резало дробные: «+31.6 °C» давало токен «316» против групп «31» и
    «6», и число источника пропадало из причины на ровном месте — а слот №4 на живых моделях
    пишет именно «море +26.5 °C».
    """

    haystack = _known_numbers(known)

    def keep(match: re.Match[str]) -> str:
        number = re.match(r"\d[\d\s]*(?:[.,]\d+)?", match.group(0))
        token = re.sub(r"\s", "", number.group(0)) if number else ""
        if not token:
            return match.group(0)
        return match.group(0) if token in haystack or token.replace(",", ".") in haystack else ""

    cleaned = _MEASURE_RE.sub(keep, text)
    cleaned = _DANGLING_RE.sub("", re.sub(r"\s+", " ", cleaned))
    #: вырезанное число оставляет висячие разделители: «море , перелёт  звёзд» чинится
    #: разбором на куски, а не третьей регуляркой поверх второй
    pieces = [piece.strip(" .") for piece in re.split(r"[,;]", cleaned)]
    joined = ", ".join(piece for piece in pieces if piece)
    return f"{joined}." if cleaned.rstrip().endswith(".") and joined else joined


def mark_finalists(state: TripState, finalists: list[Candidate]) -> TripState:
    """INSPIRE: из отобранного кодом набора — три направления (слот №4).

    `ref` слота №4 merging-ится в кандидата, поэтому строка модели идёт через
    `_without_invented_numbers`: иначе число, которое модель придумала сама, попадает в
    `TripState` и оттуда в стога, с которым `unverified_numbers` сверяет объяснение, — то
    есть guard начинает проверять модель по её же тексту.
    """

    by_id = {candidate.candidate_id: candidate for candidate in finalists}
    next_state = state.next_version()
    updated: list[Candidate] = []
    for candidate in next_state.candidates.destinations:
        chosen = by_id.get(candidate.candidate_id)
        if chosen is None:
            updated.append(candidate)
            continue
        given = {key: value for key, value in chosen.ref.items()
                 if key != "why" and key != "finalist"}
        ref = {**candidate.ref, **given, "finalist": "true"}
        if "why" in chosen.ref:
            ref["why"] = _without_invented_numbers(chosen.ref["why"], candidate.ref)
        updated.append(candidate.model_copy(update={"ref": ref}))
    next_state.candidates.destinations = updated
    return next_state


def compose(state: TripState, search: SearchResult, *, now: datetime) -> Composition:
    """COMPOSE: совместимая комбинация, а не «лучший отель + лучший рейс» (§6).

    Совместимость считает код: даты, вместимость, `min_nights`, наличие мест, hard-лимит
    часов в дороге, питомец. Выбор среди совместимых — детерминированный (дешевле, затем
    короче в дороге), потому что трейс хода обязан быть воспроизводимым; «почти подходящий»
    вариант вместо отказа не появляется (инвариант №2).
    """

    request = state.request
    nights = request.nights.value
    adults = request.party.adults.value
    if nights is None or nights < 1 or adults is None:
        #: `nights < 1` — не декоративная граница: модель отдала start=end из «в ноябре»,
        #: ставок в выдаче ровно столько же, сколько ночей (то есть ноль), `_hotel_fits`
        #: считал `len(stays) == nights` выполненным, и план собрался из перелёта и
        #: трансфера без единой ночи в отеле. Валидатор этого тоже не видит: сравнивать
        #: `len(plan.stays)` с нулём не на что.
        return Composition(
            plan=None,
            blocking=[make(ViolationCode.CANDIDATES_EMPTY, "plan",
                           expected="как минимум одна ночь и состав гостей",
                           actual=f"nights={nights}, adults={adults}")],
        )
    children = list(request.party.children.value or [])
    guests = adults + len(children)
    has_pet = request.party.pets.value is True
    max_hours = request.transport.max_hours_direct.value
    includes = [include.value for include in (request.budget.includes.value or [])]

    #: отказ по первому же финалисту означал бы «направления кончились», хотя в выдаче их
    #: трое: фикстура `transport_search` обещает пропускать направление без данных и брать
    #: следующего, и сид держит 12 направлений без единого рейса. Каждый сбой копится в
    #: `failures`; если план не собрался нигде, отдаём первый из них — финалисты упорядочены
    #: по релевантности, значит отказ относится к самому подходящему направлению и его числа
    #: читателю полезнее остальных.
    failures: list[Composition] = []

    for destination in _finalists(state):
        code = destination.candidate_id
        offers = [offer for offer in search.transport if offer.destination_code == code]
        hotels = [offer for offer in search.hotels if offer.destination_code == code]
        transfers = [offer for offer in search.transfers if offer.destination_code == code]
        within_limit = [offer for offer in offers if max_hours is None or offer.hours <= max_hours]
        fitting = [
            offer for offer in hotels if _hotel_fits(offer, guests=guests, nights=nights, has_pet=has_pet)
        ]

        if not offers:
            failures.append(_empty("transport", code, expected="рейсы на эти даты",
                                   actual="ни одного", meta=_meta_for("transport", search.meta)))
            continue
        if not within_limit:
            shortest = min(offer.hours for offer in offers)
            failures.append(Composition(
                plan=None,
                blocking=[make(
                    ViolationCode.HARD_CONSTRAINT_UNSATISFIED, "transport",
                    expected=f"{max_hours} ч", actual=f"{shortest} ч",
                    field_path="transport.max_hours_direct",
                    evidence={"destination": code, "shortest_available": f"{shortest} ч"},
                )],
            ))
            continue
        if not fitting:
            failures.append(_empty(
                "hotel", code, expected=f"номер на {guests} гостей и {nights} ночей",
                actual=f"не подошёл ни один из {len(hotels)}", meta=_meta_for("hotel", search.meta)))
            continue
        if not transfers:
            failures.append(_empty("transfer", code, expected="трансфер от прилёта",
                                   actual="ни одного", meta=_meta_for("transfer", search.meta)))
            continue
        #: источник привязал трансферы к отелям (`data/seeds/transfers.json` = одна строка на
        #: гостиницу) — отель без своего трансфера несовместим: пришить ему путь от аэропорта
        #: соседней гостиницы значит собрать план из двух разных поездок
        linked = {offer.hotel_id for offer in transfers if offer.hotel_id}
        if linked:
            own_transfer = [offer for offer in fitting if offer.specs.hotel_id in linked]
            if not own_transfer:
                failures.append(_empty(
                    "transfer", code, expected="трансфер от прилёта к выбранному отелю",
                    actual=f"ни у одного из {len(fitting)}", meta=_meta_for("transfer", search.meta)))
                continue
            fitting = own_transfer

        def transfer_for(hotel: HotelOffer) -> TransferOffer:
            """Трансфер именно этого отеля; порядок — только для источника без связи строк."""

            return next((offer for offer in transfers
                         if offer.hotel_id == hotel.specs.hotel_id), transfers[0])

        def reachable(hotel: HotelOffer) -> list[TransportOffer]:
            """Рейсы этого направления, при которых успеваешь на заселение в этот отель."""

            return [offer for offer in within_limit
                    if _reaches_checkin(offer, hotel, transfer_for(hotel))]

        def leg_for(hotel: HotelOffer) -> TransportOffer:
            """Рейс под этот отель: дешевле всех из успевающих на заселение.

            Без этого COMPOSE отдаёт «самый дешёвый рейс + первый отель по релевантности», а
            валидатор ловит стыковку постфактум: на 40 рейсах направления план падал бы из-за
            прилёта в 20:17, хотя совместимый с дедлайном рейс в выдаче был — и отказывал бы
            пользователь не получил бы ни плана, ни числа. Пустой список здесь уже невозможен:
            отели без успевающего рейса отсеяны до выбора.
            """

            pool = reachable(hotel) or within_limit
            return sorted(pool, key=lambda offer: (offer.price_total, offer.hours))[0]

        #: отель, до которого нельзя долететь до дедлайна заселения, не «почти подходящий»:
        #: собрать его и получить blocking-нарушение в VALIDATE — значит отнять у пользователя
        #: план следующего финалиста, который собрался бы. Такое направление отпускаем дальше,
        #: назвав числа в отказе
        timely = [hotel for hotel in fitting if reachable(hotel)]
        if not timely:
            earliest = min(offer.arrival.arr_at for offer in within_limit)
            deadline = min(hotel.specs.check_in_deadline for hotel in fitting)
            failures.append(_empty(
                "transport", code, expected=f"прилёт + трансфер до заселения (раньше {deadline})",
                actual=f"{len(within_limit)} рейсов, самый ранний прилёт {earliest:%H:%M}",
                meta=_meta_for("transport", search.meta)))
            continue

        hotel_order = sorted(timely, key=_hotel_order(state))

        def build(hotel: HotelOffer) -> Plan:
            leg, transfer = leg_for(hotel), transfer_for(hotel)
            return Plan(
                destination_code=code,
                nights=nights,
                adults=adults,
                children=children,
                has_pet=has_pet,
                party_total=guests,
                hotel=hotel.specs,
                stays=hotel.stays,
                legs=[leg.arrival, leg.departure],
                transfers=[transfer.arrival, transfer.departure],
                now=now,
            )

        hotel, displaced = _within_budget_or_best(hotel_order, build, request=request,
                                                  includes=includes, guests=guests)
        plan = build(hotel)
        leg, transfer = leg_for(hotel), transfer_for(hotel)
        facts = {
            "headline": f"{destination.ref['country']}, {destination.ref['name']}",
            "sea_temp_c": destination.ref.get("sea_temp_c", ""),
            "day_temp_c": destination.ref.get("day_temp_c", ""),
            "climate_month": destination.ref.get("climate_month", ""),
            "flight_h": f"{leg.arrival.duration_h}",
            "hotel": hotel.name,
        }
        if displaced is not None:
            #: мягкое предпочтение уступило hard-бюджету — это компромисс, и он обязан быть
            #: назван, а не промолчан (инвариант №2 в обратную сторону)
            facts["cheaper_than_relevant"] = displaced.name
        return Composition(
            plan=plan,
            lines=_price_lines(plan, includes),
            refs={
                "destination": code,
                "hotel": hotel.specs.hotel_id,
                "transport_in": leg.arrival.leg_id,
                "transport_out": leg.departure.leg_id,
                "transfer_arrival": transfer.arrival.transfer_id,
                "transfer_departure": transfer.departure.transfer_id,
            },
            facts=facts,
        )

    if failures:
        return failures[0]
    return _empty("destination", "", expected="хотя бы одно направление",
                  actual="финалистов нет", meta=_meta_for("destination", search.meta))


def _reaches_checkin(offer: TransportOffer, hotel: HotelOffer,
                     transfer: TransferOffer, *, buffer_min: int = 30) -> bool:
    """Успеет ли турист на заселение этим рейсом в этот отель.

    То же окно, что у валидатора (`_deadline_datetime` + `arrival_buffer_min`): сборка обязана
    сравнивать с тем же числом, иначе она «чинит» то, чего проверка не видит, и в план уходит
    пара, которая через секунду стала бы blocking-нарушением.
    """

    arrival = offer.arrival.arr_at
    try:
        hour, minute = (int(part) for part in hotel.specs.check_in_deadline.split(":"))
    except ValueError:
        return True
    deadline = arrival.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return arrival + timedelta(minutes=buffer_min + transfer.arrival.minutes) <= deadline


def _within_budget_or_best(
    ordered: list[HotelOffer],
    build: Callable[[HotelOffer], Plan],
    *,
    request: TripRequest,
    includes: list[str],
    guests: int,
) -> tuple[HotelOffer, HotelOffer | None]:
    """Первый отель по релевантности, при котором план влезает в бюджет.

    Без этого мягкое поле делает выполнимый запрос невыполнимым: самый «атмосферный» отель
    поднимает цену выше лимита, валидатор отвечает `BUDGET_EXCEEDED`, и ход заканчивается
    отказом, хотя совместимый план существовал. Soft-предпочтение имеет право менять только
    порядок перебора, но не превращать hard-ограничение в нарушение (инвариант №2).

    Если в бюджет не влезает ни один отель, берётся лучший по релевантности с
    `displaced=None`: превышение тогда честно ловит валидатор числами, а не прячется здесь.
    """

    #: список непустой: `fitting` проверен вызывающим кодом до обращения сюда
    best = ordered[0]
    budget = request.budget.amount.value
    if budget is None:
        return best, None
    limit = (budget.times(guests) if request.budget.scope.value is BudgetScope.PER_PERSON
             else budget)

    for hotel in ordered:
        lines = _price_lines(build(hotel), includes)
        total = sum((line.amount.amount for line in lines), Decimal(0))
        if total <= limit.amount:
            #: уступили месту более дешёвому — тот, что был выше по теме, и есть компромисс
            return hotel, None if hotel is best else best
    return best, None


def commit_plan(state: TripState, composition: Composition) -> TripState:
    """Строки бюджета плана уходят в `chosen` — оттуда их берёт `BudgetLedger`.

    `included_in_budget` уже решён по `budget.includes`, поэтому итог ledger — функция от
    этих линий, а не отдельное число.
    """

    if composition.plan is None:
        raise ValueError("commit_plan requires a composed plan")
    next_state = state.next_version()
    next_state.chosen.items = list(composition.lines)
    next_state.chosen.component_refs = dict(composition.refs)
    next_state.stage = Stage.COMPOSE
    logger.info("plan composed trip=%s hotel=%s lines=%d version=%d", state.trip_id,
                composition.refs.get("hotel"), len(composition.lines), next_state.version)
    return next_state


def record_violations(state: TripState, violations: list[Violation]) -> TripState:
    """VALIDATE: нарушения — структура в состоянии, а не текст (T-03)."""

    next_state = state.next_version()
    conflicts = [violation for violation in state.violations
                 if violation.code == ViolationCode.CONFLICTING_STATE]
    next_state.violations = list(violations) + [
        violation for violation in conflicts if violation not in violations
    ]
    next_state.stage = Stage.VALIDATE
    logger.info("validated trip=%s violations=%d blocking=%d", state.trip_id, len(next_state.violations),
                sum(1 for violation in next_state.violations if violation.is_blocking))
    return next_state


def set_stage(state: TripState, stage: Stage) -> TripState:
    """Версия растёт и на фазе, где решение не меняется (SHORTLIST, EXPLAIN, PLAN)."""

    next_state = state.next_version()
    next_state.stage = stage
    return next_state


def finish(state: TripState, outcome: Outcome, *, wall_clock_ms: int) -> TripState:
    """Терминал хода: любой исход — валидный ответ пользователю, а не исключение."""

    next_state = state.next_version()
    next_state.outcome = outcome
    if outcome == Outcome.PLAN_READY:
        next_state.stage = Stage.PLAN
    next_state.run_status = RunStatus.COMPLETED
    next_state.budgets.wall_clock_ms = wall_clock_ms
    return next_state


def checked_out(state: TripState, order_id: str) -> TripState:
    """CHECKOUT по подтверждению пользователя; заказ — в `chosen.component_refs`."""

    next_state = state.next_version()
    next_state.stage = Stage.CHECKOUT
    next_state.chosen.component_refs = {**state.chosen.component_refs, "order": order_id}
    return next_state


# ------------------------------------------------------- слияние и производные


def merge_request(base: TripRequest, delta: TripRequest) -> tuple[TripRequest, list[Violation]]:
    """Явное утверждение побеждает; сказанное дважды с расхождением — не «последнее wins».

    Отдельное правило: подстановка кода (`defaulted`) и вывод (`inferred`) не перезаписывают
    то, что пользователь уже назвал. Иначе второе сообщение без «вдвоём» молча превратило
    бы `party.adults` из `source="user"` в `source="default"` — provenance hard-поля
    потерялось бы, а с ним и ответ на «откуда взялось это ограничение».
    """

    merged = base.model_copy(deep=True)
    conflicts: list[Violation] = []
    for path, new_field in delta.iter_fields():
        if not _carries(new_field):
            continue
        old = base.field_value(path)
        if isinstance(old, FieldValue) and isinstance(new_field, FieldValue):
            if _differs(old, new_field):
                conflicts.append(make(
                    ViolationCode.CONFLICTING_STATE, "request",
                    expected=_plain(old.value), actual=_plain(new_field.value), field_path=path,
                ))
                new_field = new_field.model_copy(update={"needs_confirmation": True})
            elif _keeps_user_word(old, new_field):
                continue
        _set(merged, path, new_field)
    return merged, conflicts


def _keeps_user_word(old: FieldValue[object], new: FieldValue[object]) -> bool:
    return (old.status == FieldStatus.KNOWN and old.source == "user" and old.value == new.value
            and new.status in {FieldStatus.DEFAULTED, FieldStatus.INFERRED})


def derive_defaults(request: TripRequest) -> TripRequest:
    """Что считает код, а не модель: ночи из дат, комнаты из состава, безопасные дефолты."""

    derived = request.model_copy(deep=True)
    start, end = derived.dates.start.value, derived.dates.end.value
    if start is not None and end is not None:
        ours = (derived.nights.is_unknown
                or (derived.nights.status == FieldStatus.INFERRED and derived.nights.source == "dates"))
        if ours:
            #: пересчёт, а не «первый раз»: ночи — производное от дат, и витеют вместе с ними.
            #: Живой контур 2026-10-27 посчитал 0 из `start=end` («в ноябре»), потом ответ на
            #: вопрос про даты поставил 14→21, а условие «только если unknown» оставило 0 в
            #: hard-поле — план собирался из перелёта без ночёвки.
            derived.nights = FieldValue[int].inferred(
                (end - start).days, kind=ConstraintKind.HARD, source="dates", confidence=1.0
            )
    if derived.lodging.rooms.is_unknown:
        people = (derived.party.adults.value or 2) + len(derived.party.children.value or [])
        derived.lodging.rooms = FieldValue[int].defaulted(1 if people <= 3 else 2, kind=ConstraintKind.HARD)
    if derived.budget.scope.is_unknown:
        derived.budget.scope = FieldValue[BudgetScope].defaulted(BudgetScope.TOTAL, kind=ConstraintKind.HARD)
    if derived.budget.includes.is_unknown:
        derived.budget.includes = FieldValue[list[BudgetInclude]].defaulted(
            list(SAFE_INCLUDES), kind=ConstraintKind.HARD
        )
    if derived.dates.flexibility_days.is_unknown:
        derived.dates.flexibility_days = FieldValue[int].defaulted(0, kind=ConstraintKind.SOFT)
    return derived


def changed_fields(old: TripRequest, new: TripRequest) -> set[str]:
    """Пути полей, которые дельта правда поменяла; имена — из `iter_fields()`.

    Дельту по датам собирает рантайм: если сдвинулся хотя бы один месяц вилки, добавляется
    `dates.month`, иначе подбор направлений и климат ошибутся (сдвиг внутри месяца трогает
    только перелёты и отели).
    """

    paths: set[str] = set()
    for path, field_value in new.iter_fields():
        before = old.field_value(path)
        if _carries(field_value) and before != field_value:
            paths.add(path)
    #: климат и подбор направлений берут один месяц на всю вилку (`dates.month`), поэтому
    #: сравниваем множество месяцев поездки, а не один месяц выезда: сдвиг «28.10–05.11» →
    #: «01.11–08.11» выездной месяц не меняет, а погода за окном — меняет
    months_before, months_after = stay_months(old), stay_months(new)
    if months_after and months_before != months_after:
        paths.add("dates.month")
    return paths


# ---------------------------------------------------------------- вспомогательное


def _destination_candidate(fact: DestinationFact) -> Candidate:
    return Candidate(
        candidate_id=fact.code,
        kind="destination",
        ref={
            "name": fact.name,
            "country": fact.country,
            "sea_temp_c": _shown(fact.sea_temp_c),
            "day_temp_c": "",
            "flight_h": fact.flight_h,
            "est_total": "" if fact.est_total is None else fact.est_total.as_display(),
            "source_id": fact.source_id,
        },
        score=fact.score,
        data_status=fact.data_status,
    )


def _shown(value: Decimal | None) -> str:
    """Число источника для `ref`: молчание — пустая строка, а не слово «None».

    Строка нужна и для `unverified_numbers` (он сверяет цифры текста с `ref`), и для карточки:
    там отсутствие числа означает «не показывать про море», а не «показать 0».
    """

    return "" if value is None else str(value)


def _finalists(state: TripState) -> list[Candidate]:
    picked = [candidate for candidate in state.candidates.destinations if candidate.ref.get("finalist") == "true"]
    return sorted(picked, key=lambda candidate: -(candidate.score or 0.0))


def _hotel_fits(offer: HotelOffer, *, guests: int, nights: int, has_pet: bool) -> bool:
    specs = offer.specs
    if specs.capacity is not None and specs.capacity < guests:
        return False
    if has_pet and not specs.pet_friendly:
        return False
    if len(offer.stays) != nights:
        return False
    return all(stay.rate_present and stay.availability and stay.min_nights <= nights for stay in offer.stays)


def _stay_total(offer: HotelOffer) -> Decimal:
    return sum((stay.price.amount.amount for stay in offer.stays), Decimal(0))


def _hotel_order(state: TripState) -> Callable[[HotelOffer], tuple[float, Decimal]]:
    """Ключ выбора отеля: мягкое предпочтение (слот №3) важнее цены, цена — тай-брейк.

    Совпадение по релевантности до шага 0.05 даёт равенство ключей, поэтому среди «одинаково
    атмосферных» снова решает цена — детерминизм трейса не зависит от того, сколько знаков
    вернул cross-encoder. Нормализованный балл зависит от состава вызова (минимум и максимум
    этого набора), поэтому один и тот же список кандидатов даёт одно и то же число: вход
    прогона фиксирован, и повтор вызова на том же входе воспроизводим.

    Когда `relevance` не проставлен ни у кого, ключ сводится к `(_stay_total,)`: то есть
    поведение ровно такое, как до врезки слота №3. Офлайн-прогон и прогон с выключенным или
    недоступным реранком обязаны совпадать, иначе воспроизводимость прогона зависит от сети.
    """

    scores: dict[str, float] = {}
    for candidate in state.candidates.hotels:
        raw = candidate.ref.get("relevance", "")
        try:
            scores[candidate.candidate_id] = float(raw)
        except ValueError:
            #: мусор в `ref` не должен уронить сборку плана: считаем, что слот №3 не ответил
            continue
    return lambda offer: (-scores.get(offer.specs.hotel_id, 0.0), _stay_total(offer))


def _price_lines(plan: Plan, includes: list[str]) -> list[PriceLine]:
    """Все строки плана; `included_in_budget` решает `budget.includes`, а не модель."""

    def included(kind: PriceKind) -> bool:
        if kind == PriceKind.ACTIVITY:
            return BudgetInclude.ACTIVITIES.value in includes
        return kind.value in includes

    lines: list[PriceLine] = [stay.price for stay in plan.stays]
    lines += [leg.price for leg in plan.legs if leg.price is not None]
    lines += [transfer.price for transfer in plan.transfers if transfer.price is not None]
    return [line for line in lines if included(line.kind)]


#: счётчики источника сгруппированы по тому, чего не нашлось: отказ обязан называть числа
#: своего среза, а не весь журнал прогона (61 направление + 1296 отелей в одной строке читаются
#: как «мы не поняли, где мимо промахнулись»)
_META_KEYS: dict[str, tuple[str, ...]] = {
    "destination": ("направлений в каталоге", "направлений в выдаче", "направлений названо",
                    "месяц поездки"),
    "transport": ("сегментов в сиде", "аэропортов выезда нашли", "рейсов в выдаче",
                  "режимов в сиде"),
    "hotel": ("отелей в каталоге", "отелей на направление", "отелей в выдаче"),
    "transfer": ("строк трансфера в сиде", "трансферов в выдаче"),
}


def _meta_for(component: str, meta: Mapping[str, str]) -> dict[str, str]:
    """Счётчики ответа источника, относящиеся к отказавшему срезу."""

    return {key: meta[key] for key in _META_KEYS.get(component, ()) if key in meta}


def refusal_text(violations: Sequence[Violation]) -> str:
    """Отказ по-русски из тех же чисел, что уже лежат в состоянии.

    `infeasible` без строки — молчание вместо ответа «почему»: человек видит код нарушения.
    Текст собирает код из `expected/actual/evidence` (модель на отказ не тратится, слот №5
    работает на собранном плане), поэтому цифра в нём обязана быть уже в состоянии — ровно то
    же требование, по которому `unverified_numbers` сверяет объяснение плана.
    """

    lines: list[str] = []
    for violation in violations:
        if not violation.is_blocking:
            continue
        where = violation.affected_component
        numbers = [f"{label} — {value}" for label, value in violation.evidence.items()
                   if label != "destination" and value]
        tail = f"; {', '.join(numbers)}" if numbers else ""
        lines.append(f"{where}: ждали {violation.expected}, вышло {violation.actual}{tail}")
    if not lines:
        return "Плана нет: совместимых вариантов не нашлось."
    return f"Плана нет. {'; '.join(lines)}."


def _empty(component: str, code: str, *, expected: str, actual: str,
           meta: Mapping[str, str] | None = None) -> Composition:
    """Отказ сборки с числами источника.

    `CANDIDATES_EMPTY` без счётчиков — это «мы ничего не нашли» без ответа «а сколько
    было»: отказ по контракту обязан быть с посчитанными числами, а посчитать их может
    только сам источник (`SearchResult.meta`).
    """

    return Composition(
        plan=None,
        blocking=[make(ViolationCode.CANDIDATES_EMPTY, component,
                       expected=expected, actual=actual,
                       evidence={"destination": code, **(meta or {})})],
    )


_Fact = DestinationFact | ClimateFact | TransportOffer | HotelOffer | TransferOffer


def _status(rows: Sequence[_Fact]) -> DataStatus:
    if not rows:
        return DataStatus.UNKNOWN
    statuses = {row.data_status for row in rows}
    if len(statuses) == 1:
        return next(iter(statuses))
    return DataStatus.PARTIALLY_VERIFIED


def _carries(field_value: object) -> bool:
    if isinstance(field_value, FieldValue):
        return field_value.status != FieldStatus.UNKNOWN
    if isinstance(field_value, list):
        return bool(field_value)
    return False


def _differs(old: FieldValue[object], new: FieldValue[object]) -> bool:
    if old.is_unknown or old.status in {FieldStatus.INFERRED, FieldStatus.DEFAULTED}:
        return False
    return old.value != new.value


def stay_months(request: TripRequest) -> set[tuple[int, int]]:
    """Месяцы, которые поездка правда задевает: `(год, месяц)` каждой ночи."""

    return set(_month_nights(request))


def _month_nights(request: TripRequest) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    start, end = request.dates.start.value, request.dates.end.value
    if start is None or end is None or end <= start:
        return counts
    day = start
    while day < end:
        key = (day.year, day.month)
        counts[key] = counts.get(key, 0) + 1
        day += timedelta(days=1)
    return counts


def stay_month(request: TripRequest) -> str:
    """Производный аргумент реестра (`dates.month`): месяц, в котором больше всего ночей.

    Источник климата ключуется одной парой `(dest_code, month)` — `data/seeds/climate.json`,
    — а вилка «с 28 октября по 5 ноября» лежит в двух месяцах. Раньше брался месяц выезда,
    и семидневная октябрьская поездка получала ноябрьскую температуру; при равенстве ночей
    берётся более ранний месяц, потому что пользователь называет именно его («в октябре»).
    """

    counts = _month_nights(request)
    if not counts:
        end = request.dates.end.value
        return "" if end is None else f"{end.year:04d}-{end.month:02d}"
    year, month = min(counts, key=lambda key: (-counts[key], key))
    return f"{year:04d}-{month:02d}"


def destination_codes(state: TripState) -> list[str]:
    """Производный аргумент реестра (`destinations`): финалисты, а до них — весь отбор."""

    picked = _finalists(state)
    codes = [candidate.candidate_id for candidate in picked]
    if codes:
        return sorted(codes)
    return sorted(candidate.candidate_id for candidate in state.candidates.destinations)


def arrival_time(state: TripState) -> str:
    """Производный аргумент реестра (`transport.arrival_time`): прилёт inbound-рейса."""

    for candidate in state.candidates.transport:
        if candidate.ref.get("component") == "transport_in":
            return candidate.ref.get("arr_at", "")
    return ""


def _set(request: TripRequest, path: str, value: object) -> None:
    """Путей полей много и они заданы контрактом — пишем по имени из `iter_fields()`."""

    section, _, name = path.partition(".")
    if name:
        setattr(getattr(request, section), name, value)
        return
    if section in SECTIONS or section in LEAVES:
        setattr(request, section, value)
        return
    if section == "soft_preferences":
        added = [str(item) for item in value] if isinstance(value, list) else []
        phrases = list(request.soft_preferences)
        phrases.extend(phrase for phrase in added if phrase not in phrases)
        request.soft_preferences = phrases
        return
    raise ValueError(f"unknown request field path: {path}")


def _plain(value: object) -> str:
    if isinstance(value, Money):
        return value.as_display()
    return "" if value is None else str(value)


def _require_not_skipped(path: str, raw: str) -> None:
    if raw.strip().lower() in {"", "пропустить", "skip", "-"}:
        raise ValueError(f"{path} is hard-required: skipping is not allowed")


def _apply_dates(request: TripRequest, raw: str) -> None:
    """Один вопрос про вилку дат закрывает `dates.start`, `dates.end` и `nights`.

    Диапазон читает общий `range_days` — тот же разборщик, что у мока T-05 и у коэрсера
    живой цитаты. Раньше здесь был свой `month.replace(day=...)`, и он не умел ни пересечение
    месяца («с 28 по 5» → `end <= start` → ход падал на втором вопросе), ни `дд.мм`
    («28.10 - 05.11» читалось как дни 28 и 10 того же месяца).
    """

    if raw.strip().lower() in {"", "пропустить", "skip", "-"}:
        anchor = date(2026, 11, 1)
        request.dates.start = FieldValue[date].defaulted(anchor.replace(day=14), kind=ConstraintKind.HARD)
        request.dates.end = FieldValue[date].defaulted(anchor.replace(day=21), kind=ConstraintKind.HARD)
        return
    said_start = request.dates.start.value
    span = range_days(raw, year=(said_start or date(2026, 11, 1)).year,
                      month=(said_start or date(2026, 11, 1)).month)
    if span is None:
        raise ValueError(f"cannot read a date range from {raw!r}")
    start, end = span
    if end <= start:
        raise ValueError(f"date range is empty: {start} → {end}")
    request.dates.start = FieldValue[date].known(start, kind=ConstraintKind.HARD)
    request.dates.end = FieldValue[date].known(end, kind=ConstraintKind.HARD)


def _money(raw: str) -> Money | None:
    lowered = raw.strip().lower()
    if match := re.search(r"(\d{1,3})\s*[кk]\b", lowered):
        return Money(amount=Decimal(match.group(1)) * 1000, currency="RUB")
    compact = lowered.replace(" ", "").replace("\u00a0", "")
    if match := re.search(r"\d{2,7}", compact):
        return Money(amount=Decimal(match.group(0)), currency="RUB")
    return None


def _hours(raw: str) -> float | None:
    match = re.search(r"(\d{1,2})(?:[.,](\d))?", raw.strip())
    if match is None:
        return None
    return float(f"{match.group(1)}.{match.group(2) or 0}")
