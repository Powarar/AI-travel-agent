"""Гейт второй волны (offline fixture): сквозной золотой путь через рантайм.

Здесь нет сети и нет настоящей модели: источники — заглушки над фикстурами, слоты — моки
с фиксированными ответами. Проверяется контур и его инварианты:

* ≤2 вопроса, ≤12 tool-вызовов, ≤6 LLM-вызовов, `wall_clock` из `time.monotonic()`;
* interrupt переживает рестарт процесса, повторный ответ не создаёт второй ход;
* ни одно число объяснения не выдумано (`unverified_numbers` против `TripState`/`BudgetLedger`);
* hard-ограничение не ослабляется молча (инвариант №2) и не переспрашивается источник на
  ступени 0 (рантайм слушает `tools_for`).

Числа фикстур посчитаны вручную и стоят в ассертах: 7 × 11 200 + (21 500 + 22 800) +
(1 200 + 1 300) = 125 200 ₽ при бюджете 150 000 ₽.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.agent.app.llm.slots import Explanation, MockSlots
from services.agent.app.orchestration.nodes import (
    ClimateFact,
    DestinationFact,
    HotelOffer,
    SearchResult,
    TransportOffer,
    TransferOffer,
    compose,
    derive_defaults,
)
from services.agent.app.orchestration.runtime import Runtime, unverified_numbers
from services.agent.app.validation.inputs import (
    HotelSpecs,
    Plan,
    StayNight,
    TransferLeg,
    TransportLeg,
)
from shared.contracts.enums import (
    ConstraintKind,
    CostScope,
    DataStatus,
    DirectionMode,
    FieldStatus,
    Outcome,
    PriceKind,
    RunStatus,
    Stage,
    ViolationCode,
)
from shared.contracts.money import BudgetLedger, Money, PriceLine
from shared.contracts.request import FieldValue
from shared.contracts.state import TripState

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
FETCHED = NOW - timedelta(days=2)
MESSAGE = "хочу к тёплому морю в ноябре вдвоём с ребёнком, 150к, без долгих перелётов"

#: (код, город, страна, вода °C, часы в пути, оценка источника) — 12 направлений, из них
#: слот №4 оставляет три: у них есть перелёты, отели и трансферы.
DESTINATIONS: tuple[tuple[str, str, str, int, str, int, float], ...] = (
    ("AYT", "Анталья", "Турция", 24, "3.5", 125200, 0.92),
    ("HKT", "Пхукет", "Таиланд", 29, "9.0", 168000, 0.88),
    ("HER", "Ираклион", "Греция", 22, "5.5", 118000, 0.85),
    ("GOA", "Даболим", "Индия", 30, "8.0", 158000, 0.80),
    ("DAD", "Дананг", "Вьетнам", 27, "9.5", 149000, 0.74),
    ("CIA", "Краснодар", "Россия", 21, "2.0", 61000, 0.70),
    ("SPC", "Санта-Крус", "Тенерифе", 23, "7.0", 141000, 0.66),
    ("BUH", "Бурса", "Турция", 19, "4.0", 98000, 0.61),
    ("VTB", "Витоша", "Болгария", 18, "3.0", 72000, 0.57),
    ("TAA", "Таобок", "Филиппины", 28, "11.0", 176000, 0.52),
    ("KUS", "Кусадасы", "Турция", 20, "3.8", 104000, 0.47),
    ("NOR", "Норрчёпинг", "Швеция", 12, "2.5", 89000, 0.41),
)
#: код → (часы туда, ₽ туда, часы обратно, ₽ обратно)
FLIGHTS: dict[str, tuple[float, int, float, int]] = {
    "AYT": (3.5, 21500, 4.0, 22800),
    "HKT": (9.0, 41000, 9.5, 43500),
    "HER": (5.5, 26000, 5.5, 27000),
}
#: код → отели: (id, название, звёзды, вместимость, ₽ за ночь, можно с животными, удобства)
#: Удобства — slug-и из vocabulary сида (`data/generators/hotels.py`, AMENITIES): слоту №3
#: нечем скорить «атмосферный», если в тексте кандидата остаются только вывеска и звёзды.
HOTELS: dict[str, tuple[tuple[str, str, int, int, int, bool, tuple[str, ...]], ...]] = {
    "AYT": (
        ("AYT-FAM", "Aqua Family Resort", 4, 4, 11200, True,
         ("pool", "kids_club", "breakfast", "beach_first_line")),
        ("AYT-LUX", "Gulf Palace", 5, 4, 19500, True,
         ("spa", "beach_first_line", "restaurant", "breakfast", "pool")),
        ("AYT-STD", "Old Town Studio", 3, 2, 8900, True,
         ("wifi", "air_conditioning", "laundry")),
    ),
    "HKT": (("HKT-ONE", "Andaman Bay", 4, 5, 15400, True,
             ("beach_first_line", "spa", "pool", "breakfast")),),
    "HER": (("HER-ONE", "Creta Marina", 4, 3, 10900, False,
             ("restaurant", "air_conditioning", "parking")),),
}
#: код → (минуты от прилёта, ₽, минуты до вылета, ₽)
TRANSFERS: dict[str, tuple[int, int, int, int]] = {
    "AYT": (40, 1200, 45, 1300),
    "HKT": (60, 1800, 70, 2000),
    "HER": (35, 1100, 40, 1200),
}

HOTEL_SOURCE = "ostrovok-fixture"
FLIGHT_SOURCE = "flight-fixture"
TRANSFER_SOURCE = "transfer-fixture"
CLIMATE_SOURCE = "climate-fixture"


def _money(value: int) -> Money:
    return Money(amount=Decimal(value), currency="RUB")


def _line(line_id: str, kind: PriceKind, value: int, scope: CostScope, source_id: str) -> PriceLine:
    return PriceLine(
        line_id=line_id,
        kind=kind,
        amount=_money(value),
        scope=scope,
        included_in_budget=True,
        source_id=source_id,
        fetched_at=FETCHED,
        data_status=DataStatus.SYNTHETIC,
    )


def _leg(args: Mapping[str, object], code: str, *, component: str, hours: float,
         price: int, start: date, end: date) -> TransportLeg:
    """Прилёт — в 11:40 дня заезда, вылет — в 19:40 дня выезда: стыковка считается кодом."""

    if component == "transport_in":
        dep_at = datetime.combine(start, datetime.min.time()).replace(hour=8, tzinfo=UTC)
        arr_at = datetime.combine(start, datetime.min.time()).replace(hour=11, minute=40, tzinfo=UTC)
    else:
        dep_at = datetime.combine(end, datetime.min.time()).replace(hour=19, minute=40, tzinfo=UTC)
        arr_at = datetime.combine(end, datetime.min.time()).replace(hour=22, tzinfo=UTC)
    leg_id = f"{code}-{component}"
    return TransportLeg(
        leg_id=leg_id,
        component=component,
        dep_at=dep_at,
        arr_at=arr_at,
        duration_h=hours,
        direct=True,
        overnight=False,
        price=_line(f"transport:{leg_id}", PriceKind.TRANSPORT, price, CostScope.TOTAL, FLIGHT_SOURCE),
    )


def _stays(hotel_id: str, rate: int, start: date, nights: int) -> list[StayNight]:
    return [
        StayNight(
            date=start + timedelta(days=offset),
            price=_line(f"lodging:{hotel_id}:{start + timedelta(days=offset):%Y%m%d}",
                        PriceKind.LODGING, rate, CostScope.PER_NIGHT, HOTEL_SOURCE),
            availability=True,
            min_nights=2,
            source_id=HOTEL_SOURCE,
            fetched_at=FETCHED,
            data_status=DataStatus.SYNTHETIC,
        )
        for offset in range(nights)
    ]


def _requested_dates(args: Mapping[str, object]) -> tuple[date, date]:
    return date.fromisoformat(str(args["dates.start"])), date.fromisoformat(str(args["dates.end"]))


def _codes(args: Mapping[str, object]) -> list[str]:
    return [str(code) for code in args.get("destinations", [])]


def suggest_destinations(args: Mapping[str, object]) -> SearchResult:
    return SearchResult(
        destinations=[
            DestinationFact(
                code=code, name=name, country=country, sea_temp_c=Decimal(sea),
                flight_h=flight_h, est_total=_money(est), source_id="ideas-fixture", score=score,
            )
            for code, name, country, sea, flight_h, est, score in DESTINATIONS
        ]
    )


def weather_climate(args: Mapping[str, object]) -> SearchResult:
    sea = {row[0]: row[3] for row in DESTINATIONS}
    #: месяц — часть ответа источника: строка погоды ключуется парой `(code, month)`,
    #: и карточка обязана показывать температуру именно того месяца, в котором ночует поездка
    month = str(args.get("dates.month", ""))
    return SearchResult(
        climate=[
            ClimateFact(code=code, month=month, sea_temp_c=Decimal(sea[code]),
                        day_temp_c=Decimal(sea[code] + 2), source_id=CLIMATE_SOURCE)
            for code in _codes(args)
        ]
    )


def transport_search(args: Mapping[str, object]) -> SearchResult:
    start, end = _requested_dates(args)
    offers = []
    for code in _codes(args):
        #: нет данных о перелётах — пустая выдача, как у настоящего источника, а не KeyError:
        #: живой слот №4 вправе выбрать направление из 12, у которого у нас нет сегмента, и
        #: тогда сборка пропустит такое направление и возьмёт следующего финалиста.
        flight = FLIGHTS.get(code)
        if flight is None:
            continue
        hours_in, price_in, hours_out, price_out = flight
        offers.append(TransportOffer(
            destination_code=code,
            arrival=_leg(args, code, component="transport_in", hours=hours_in, price=price_in,
                         start=start, end=end),
            departure=_leg(args, code, component="transport_out", hours=hours_out, price=price_out,
                           start=start, end=end),
        ))
    return SearchResult(transport=offers)


def hotel_search(args: Mapping[str, object]) -> SearchResult:
    start, end = _requested_dates(args)
    nights = (end - start).days
    offers = []
    for code in _codes(args):
        for hotel_id, name, stars, capacity, rate, pet, amenities in HOTELS.get(code, ()):
            offers.append(HotelOffer(
                destination_code=code,
                name=name,
                stars=stars,
                specs=HotelSpecs(
                    hotel_id=hotel_id, pet_friendly=pet, check_in_deadline="14:00",
                    capacity=capacity, min_nights=2, source_id=HOTEL_SOURCE,
                ),
                stays=_stays(hotel_id, rate, start, nights),
                amenities=list(amenities),
                score=rate / 100000,
            ))
    return SearchResult(hotels=offers)


def transfer_search(args: Mapping[str, object]) -> SearchResult:
    offers = []
    for code in _codes(args):
        transfer = TRANSFERS.get(code)
        if transfer is None:
            continue
        minutes_in, price_in, minutes_out, price_out = transfer
        offers.append(TransferOffer(
            destination_code=code,
            arrival=TransferLeg(transfer_id=f"{code}-in", kind="arrival", minutes=minutes_in,
                                price=_line(f"transfer:{code}-in", PriceKind.TRANSFER, price_in,
                                            CostScope.TOTAL, TRANSFER_SOURCE),
                                pet_ok=True, capacity=4),
            departure=TransferLeg(transfer_id=f"{code}-out", kind="departure", minutes=minutes_out,
                                  price=_line(f"transfer:{code}-out", PriceKind.TRANSFER, price_out,
                                              CostScope.TOTAL, TRANSFER_SOURCE),
                                  pet_ok=True, capacity=4),
        ))
    return SearchResult(transfers=offers)


def checkout_mock(args: Mapping[str, object]) -> SearchResult:
    basket = dict(args["basket"])
    return SearchResult(order_id=f"ord-{basket['destination']}-{basket['hotel']}")


SOURCES: dict[str, object] = {
    "suggest_destinations": suggest_destinations,
    "weather_climate": weather_climate,
    "transport_search": transport_search,
    "hotel_search": hotel_search,
    "transfer_search": transfer_search,
    "checkout_mock": checkout_mock,
}


@pytest.fixture
def runtime() -> Runtime:
    """Чистый рантайм на тест: кэш и черновики хода не переносятся между проверками."""

    return Runtime(SOURCES)  # type: ignore[arg-type]


def walk_question(run, engine: Runtime, answer: str):
    """Ответ на висящий вопрос; путь поля берётся из `awaiting_fields`, а не по памяти."""

    field = run.state.awaiting_fields[0]
    return engine.answer(run, field, answer, now=NOW)


def golden_run(engine: Runtime):
    """Золотой путь целиком: два вопроса → план → mock-checkout."""

    run = engine.start(MESSAGE, now=NOW)
    run = walk_question(run, engine, "Москва")
    run = walk_question(run, engine, "с 14 по 21")
    return engine.checkout(run, now=NOW)


def ledger_of(state: TripState) -> BudgetLedger:
    """Ledger собирается из строк `chosen` — ровно как это делает рантайм."""

    return BudgetLedger(
        lines=list(state.chosen.items),
        budget=state.request.budget.amount.value,
        scope=state.request.budget.scope.value,
    )


def hard_field_sources(state: TripState) -> dict[str, str]:
    return {
        path: field.source
        for path, field in state.request.iter_fields()
        if isinstance(field, FieldValue) and field.kind == ConstraintKind.HARD and not field.is_unknown
    }


class TestGoldenPath:
    def test_turn_is_clarify_then_plan_ready(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        assert run.state.stage == Stage.CLARIFY
        assert run.state.run_status == RunStatus.INTERRUPTED
        assert run.state.outcome == Outcome.AWAITING_ANSWER
        assert run.state.awaiting_fields == ["origin"]
        assert run.texts["origin"]

        run = runtime.answer(run, "origin", "Москва", now=NOW)
        assert run.state.awaiting_fields == ["dates.start"]

        run = runtime.answer(run, "dates.start", "с 14 по 21", now=NOW)
        assert run.state.stage == Stage.PLAN
        assert run.state.outcome == Outcome.PLAN_READY
        assert run.state.run_status == RunStatus.COMPLETED

    def test_phases_follow_the_contour(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        # DECIDE встречается трижды: два раза он закончился вопросом (interrupt), в третий
        # раз — решением ничего не спрашивать
        assert run.trace == ["DECIDE", "DECIDE", "DECIDE", "INSPIRE", "SEARCH", "COMPOSE",
                             "VALIDATE", "SHORTLIST", "EXPLAIN", "PLAN"]

    def test_three_finalists_and_five_hotels(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        finalists = [candidate for candidate in run.state.candidates.destinations
                     if candidate.ref.get("finalist") == "true"]
        assert [candidate.candidate_id for candidate in finalists] == ["AYT", "HKT", "HER"]
        # числа карточки лежат в `ref` источника, их не выводила модель
        assert run.state.candidates.destinations[0].ref["sea_temp_c"] == "24"
        assert len(run.state.candidates.hotels) == 5
        assert run.state.provenance["hotels"] == DataStatus.SYNTHETIC

    def test_money_comes_from_the_ledger_only(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        ledger = ledger_of(run.state)
        # 7 × 11 200 + 21 500 + 22 800 + 1 200 + 1 300
        assert ledger.total.amount == Decimal("125200")
        assert ledger.remaining.amount == Decimal("24800")
        assert ledger.over_budget is None
        assert run.explanation is not None
        assert "125 200 ₽" in run.explanation
        assert "150 000 ₽" in run.explanation
        assert [line.amount.amount for line in run.state.chosen.items].count(Decimal("11200")) == 7

    def test_no_blocking_and_no_warnings(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert run.state.violations == []
        assert run.state.blocking_violations() == []

    def test_turn_limits(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        budgets = run.state.budgets
        print(f"\n  золотой путь, 3 хода: tool_calls={budgets.tool_calls} "
              f"llm_calls={budgets.llm_calls} questions={run.state.questions_asked} "
              f"wall_clock_ms={budgets.wall_clock_ms} (последний активный отрезок)")
        print(f"  source_calls={runtime.cache.source_calls} cache_hits={runtime.cache.cache_hits}")
        print(f"  объяснение: {run.explanation}")
        assert run.state.questions_asked == 2
        assert budgets.questions_asked <= budgets.questions_limit
        assert budgets.tool_calls <= budgets.tool_calls_limit
        assert budgets.llm_calls <= budgets.llm_calls_limit
        assert budgets.tool_calls == 5 + 1          # пять источников + checkout
        assert budgets.llm_calls == 5               # parse, 2 формулировки, финалисты, объяснение
        assert runtime.cache.source_calls == 6      # пять источников + mock-checkout
        assert budgets.wall_clock_ms < budgets.wall_clock_limit_ms

    def test_hard_fields_kept_their_provenance(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        sources = hard_field_sources(run.state)
        # ни одно hard-поле не сменилось «от рантайма»: разрешены только user, безопасный
        # default, вывод из дат и документированный вывод из фразы «без долгих перелётов».
        assert set(sources.values()) <= {"user", "default", "dates", "preference:short_flight"}
        assert sources["origin"] == sources["budget.amount"] == "user"
        assert sources["dates.start"] == sources["dates.end"] == "user"
        # сказанное в первом сообщении переживает два хода с ответами и mock-checkout
        assert sources["party.adults"] == "user"
        assert run.state.request.party.adults.value == 2
        # «с ребёнком» без возраста: сам ребёнок назван, а 7 лет — наш безопасный плейсхолдер,
        # и врать про него `source="user"` парсер не вправе (инвариант №3)
        assert sources["party.children"] == "default"
        assert run.state.request.party.children.value == [7]
        assert run.state.request.budget.amount.value.amount == Decimal("150000")

    def test_version_grows_once_per_node(self, runtime: Runtime) -> None:
        """Версия = число записей в состояние: каждый узел поднимает её ровно на одну.

        `Run` — один и тот же объект на ход вместе с ответами, поэтому срезы версии снимаем
        числами, а прошлое состояние держим ссылкой: оно обязано остаться нетронутым.
        """

        run = runtime.start(MESSAGE, now=NOW)
        started = run.state
        after_first = run.state.version                     # parse + вопрос про origin
        assert after_first == 2
        run = walk_question(run, runtime, "Москва")
        after_second = run.state.version                    # ответ + вопрос про даты
        assert after_second - after_first == 2
        run = walk_question(run, runtime, "с 14 по 21")
        # ответ(1) + DECIDE(1) + INSPIRE: подбор, финалисты, климат, отметка фазы (4)
        # + SEARCH: перелёты, отели, трансферы, отметка фазы (4)
        # + COMPOSE + VALIDATE + SHORTLIST + EXPLAIN + PLAN (5) = 15
        assert run.state.version - after_second == 15
        after_plan = run.state.version
        run = runtime.checkout(run, now=NOW)                # заказ + закрытие хода
        assert run.state.version - after_plan == 2
        assert run.state.version == 21
        assert started.version == after_first               # прошлые версии не мутируются

    def test_checkout_returns_an_order_and_replays(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert run.order_id == "ord-AYT-AYT-FAM"
        assert run.state.chosen.component_refs["order"] == "ord-AYT-AYT-FAM"
        before = run.state.version
        again = runtime.checkout(run, now=NOW)
        assert again.state.version == before      # повторный checkout — тот же ход, тот же заказ

    def test_composition_survives_serialization(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        payload = runtime.dump(run)
        restored = Runtime(SOURCES).load(payload)  # type: ignore[arg-type]
        assert restored.state.chosen.component_refs == run.state.chosen.component_refs
        assert restored.scratch.composition is not None


class TestInterrupt:
    def test_question_survives_process_restart(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        payload = runtime.dump(run)

        fresh = Runtime(SOURCES)                  # процесс «упал и поднялся»: кэш пуст
        restored = fresh.load(payload)
        assert restored.state.run_status == RunStatus.INTERRUPTED
        assert restored.state.awaiting_fields == ["origin"]
        assert restored.questions["origin"].field == "origin"

        restored = fresh.answer(restored, "origin", "Москва", now=NOW)
        assert restored.state.awaiting_fields == ["dates.start"]
        restored = fresh.answer(restored, "dates.start", "с 14 по 21", now=NOW)
        assert restored.state.outcome == Outcome.PLAN_READY
        assert ledger_of(restored.state).total.amount == Decimal("125200")

    def test_duplicate_answer_does_not_start_a_second_turn(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        asked = run.state                              # состояние, в котором висит вопрос
        answered = runtime.answer(run, "origin", "Москва", now=NOW)
        repeated = runtime.answer(answered, "origin", "Санкт-Петербург", now=NOW)
        assert repeated.state.version == answered.state.version
        assert str(repeated.state.request.origin.value) == "Москва"
        # второй вопрос задал контур (вилка дат), а не повторный ответ: счётчики не двинулись
        assert repeated.state.budgets.questions_asked == answered.state.budgets.questions_asked == 2
        assert repeated.state.questions_asked == answered.state.questions_asked == 2
        assert repeated.state.awaiting_fields == answered.state.awaiting_fields == ["dates.start"]
        assert asked.version == 2 and asked.awaiting_fields == ["origin"]   # версия не мутирована

    def test_answer_without_a_pending_question_is_rejected(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        with pytest.raises(ValueError, match="no pending question"):
            runtime.answer(run, "budget.amount", "200к", now=NOW)

    def test_skipping_a_hard_question_is_refused(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        with pytest.raises(ValueError, match="hard-required"):
            runtime.answer(run, "origin", "пропустить", now=NOW)

    def test_questions_stop_at_the_limit(self, runtime: Runtime) -> None:
        """Третий вопрос задать нельзя: ход идёт дальше с тем, что есть, а не падает."""

        run = runtime.start("хочу к морю", now=NOW)
        run = walk_question(run, runtime, "Москва")
        run = walk_question(run, runtime, "с 14 по 21")
        assert run.state.questions_asked == 2
        assert run.state.budgets.questions_asked == 2
        assert run.state.outcome in {Outcome.PLAN_READY, Outcome.INFEASIBLE, Outcome.BEST_EFFORT}


class TestLimitAbort:
    """Пятый пункт приёмки: любой исчерпанный лимит — исход `best_effort`, не исключение."""

    def test_wall_clock_limit_ends_the_run_with_what_exists(self) -> None:
        """Часы хода (`time.monotonic`) ушли за 20 с — контур встаёт на SEARCH и отдаёт лучшее.

        Ход прерывается там, где счётчики уже нельзя не заметить: перед каждым слотом и
        каждым инструментом. Проверка не привязана к фазе, потому что фаза зависит от того,
        на каком именно обращении часы переполнились.
        """

        ticks = itertools.count()
        engine = Runtime(SOURCES, clock=lambda: 25.0 * next(ticks))  # type: ignore[arg-type]
        run = engine.start(MESSAGE, now=NOW)
        run = walk_question(run, engine, "Москва")
        run = walk_question(run, engine, "с 14 по 21")

        assert run.state.run_status == RunStatus.COMPLETED
        assert run.state.outcome == Outcome.BEST_EFFORT
        assert "limit:wall_clock" in run.unresolved
        assert run.state.blocking_violations() == []      # остановка не притворяется нарушением
        assert run.state.request.party.adults.source == "user"   # требования целы


class TestNumberGuard:
    def test_explanation_numbers_are_all_in_state_or_ledger(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert run.explanation is not None
        assert unverified_numbers(run.explanation, run.state, ledger_of(run.state)) == []

    def test_invented_sum_is_rejected_then_template_wins(self) -> None:
        class LyingSlots(MockSlots):
            """Мок, который врёт: 152 000 ₽ вместо посчитанных 125 200 ₽."""

            def explain(self, explanation: Explanation) -> str:
                return "Отличный вариант: итого 152 000 ₽ из 150 000 ₽, остаток 24 800 ₽."

        engine = Runtime(SOURCES, slots=LyingSlots())  # type: ignore[arg-type]
        run = golden_run(engine)
        assert run.explanation is not None
        assert "152 000" not in run.explanation
        assert "125 200 ₽" in run.explanation          # шаблон собран из ledger
        assert unverified_numbers(run.explanation, run.state, ledger_of(run.state)) == []
        assert run.state.budgets.llm_calls == 6        # две попытки слота №5, третья — уже нельзя

    def test_guard_sees_a_real_hole(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert unverified_numbers("итого 148 900 ₽", run.state, ledger_of(run.state)) == ["148900"]

    def test_guard_is_substring_strong_and_no_stronger(self, runtime: Runtime) -> None:
        """Известная слабость дешёвого guard'а, зафиксирована тестом, а не комментарием.

        Большие суммы ловятся: «152 000» нет ни в состоянии, ни в ledger. Маленькие числа
        — нет: «25 ч» и «2 °C» находятся подстроками внутри честных чисел состояния
        («1 250», «24»), и пропустят выдуманную температуру или часы перелёта. Это держим
        ровно до provider integration, где guard получает настоящий вход слота, и до метрики
        «доля неподтверждённых утверждений» в evaluation layer: сравнивать числа поштучно здесь смысла
        нет, потому что вход в слот всё ещё мок.
        """

        run = golden_run(runtime)
        ledger = ledger_of(run.state)
        assert unverified_numbers("итого 152 000 ₽", run.state, ledger) == ["152000"]
        assert unverified_numbers("перелёт 25 ч, море 2 °C", run.state, ledger) == []


class TestHardConstraintIsNotRelaxedQuietly:
    """Инвариант №2: «не больше 2 ч в дороге» не превращается в 4 ч без вопроса."""

    MESSAGE = "из Москвы к тёплому морю вдвоём в ноябре с 14 по 21, бюджет 150к, не больше 2 ч в дороге"

    def test_blocks_on_a_question_with_computed_numbers(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        assert run.state.stage == Stage.CLARIFY
        assert run.state.awaiting_fields == ["transport.max_hours_direct"]
        assert run.state.chosen.items == []           # план не зафиксирован
        text = run.texts["transport.max_hours_direct"]
        # числа посчитал код из кандидатов, и предложение совпадает с посчитанным:
        # 3.5 ч — это один сегмент, туда-обратно самый короткий перелёт выходит 4.0 ч
        assert "2.0" in text and "4.0" in text
        assert "3.5" not in text
        assert run.questions["transport.max_hours_direct"].options == ("4.0",)
        assert run.state.outcome == Outcome.AWAITING_ANSWER

    def test_relaxation_is_written_as_the_users_own_words(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        run = runtime.answer(run, "transport.max_hours_direct", "не больше 4 ч", now=NOW)
        assert run.state.outcome == Outcome.PLAN_READY
        held = run.state.request.transport.max_hours_direct
        assert held.value == 4.0 and held.source == "user"
        # ни один сегмент не длиннее названной пользователем границы
        hours = [candidate.ref["duration_h"] for candidate in run.state.candidates.transport
                 if candidate.ref["destination"] == "AYT"]
        assert max(float(value) for value in hours) == 4.0

    def test_infeasible_when_the_user_will_not_answer(self, runtime: Runtime) -> None:
        """Лимит вопросов исчерпан — ослаблять hard-поле молча нельзя, отказ с числами."""

        # город и даты не названы: два вопроса уходят на них, а про «не больше 2 ч»
        # спросить уже некому (лимит на путешествие = 2)
        run = runtime.start("к тёплому морю вдвоём в ноябре, бюджет 150к, "
                            "не больше 2 ч в дороге", now=NOW)
        run = walk_question(run, runtime, "Москва")
        assert run.state.awaiting_fields == ["dates.start"]
        assert run.state.questions_asked == 2       # про город и про даты — лимит выбран
        run = walk_question(run, runtime, "с 14 по 21")

        assert run.state.outcome == Outcome.INFEASIBLE
        assert run.state.questions_asked == 2
        assert run.state.chosen.items == []           # план не зафиксирован ни на рубль
        assert run.unresolved == ["HARD_CONSTRAINT_UNSATISFIED:ask_with_computed_compromises"]
        blocking = run.state.blocking_violations()
        assert [violation.code for violation in blocking] == [ViolationCode.HARD_CONSTRAINT_UNSATISFIED]
        numbers = " ".join(
            f"{violation.expected} {violation.actual} {violation.evidence}"
            for violation in blocking
        )
        assert "2.0" in numbers and "4.0" in numbers   # отказ обязан быть с посчитанными числами
        # 4.0 — самый короткий из имеющихся перелёт туда-обратно (берётся максимум сегментов),
        # а не 3.5: рантайм честно сравнивает с ограничением пользователя, а не с сегментом


class TestNamedDestination:
    """AGENTS.md, «Названное направление»: прогон 2026-10-28, где «хочу в сочи» закончился
    планом в Анталье с пустым списком нарушений. Источник вправе вернуть своё — контур не
    вправе выдать это за выполненное hard-требование.

    Фикстура золотого пути отдаёт 12 морских направлений и не знает про Сочи — это ровно та
    ситуация, в которой подмена и происходила: теперь она ловится на входе.
    """

    MESSAGE = "хочу в сочи на неделю в ноябре, с 14 по 21, из Москвы, бюджет 150к"

    def test_named_destination_blocks_instead_of_plan_ready(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        st = run.state
        assert st.outcome == Outcome.AWAITING_ANSWER
        assert st.awaiting_fields == ["direction.explicit"]
        assert st.request.direction.explicit.value == "сочи"
        assert st.request.direction.explicit.source == "user"

        blocking = st.blocking_violations()
        assert [v.code for v in blocking] == [ViolationCode.HARD_CONSTRAINT_UNSATISFIED]
        assert blocking[0].field_path == "direction.explicit"
        assert blocking[0].expected == "сочи" and blocking[0].actual == "AYT"
        # число из источника, а не из головы: рассмотрено 12 строк, совпадений нет
        assert blocking[0].evidence["considered"] == "12"
        assert blocking[0].evidence["matched"] == "нет"

    def test_the_question_is_written_for_a_human(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        text = run.texts["direction.explicit"]
        assert "сочи" in text and "AYT" in text and "12" in text
        assert "direction.explicit" not in text          # имя поля — не ответ пользователю
        assert "why" not in text                         # и ключи `evidence` тоже
        assert run.questions["direction.explicit"].options == ("подбери сам",)

    def test_answer_with_another_name_rebuilds_in_it(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        run = runtime.answer(run, "direction.explicit", "в анталью", now=NOW)
        st = run.state
        assert st.outcome == Outcome.PLAN_READY
        assert st.request.direction.explicit.value == "в анталью"
        assert st.chosen.component_refs["destination"] == "AYT"
        assert st.violations == []                       # названное совпало — нарушения нет

    def test_answer_pick_yourself_reopens_the_choice(self, runtime: Runtime) -> None:
        run = runtime.start(self.MESSAGE, now=NOW)
        run = runtime.answer(run, "direction.explicit", "подбери сам", now=NOW)
        st = run.state
        assert st.outcome == Outcome.PLAN_READY
        assert st.request.direction.mode.value == DirectionMode.OPEN
        assert st.request.direction.explicit.value is None
        assert [v.field_path for v in st.violations] == []

    def test_no_question_left_means_infeasible_with_the_count(self, runtime: Runtime) -> None:
        """Лимит вопросов выбран — молча оставить план в другом направлении нельзя (инвариант №2)."""

        run = runtime.start("хочу в сочи вдвоём, бюджет 150к", now=NOW)
        run = walk_question(run, runtime, "Москва")
        run = walk_question(run, runtime, "с 14 по 21")
        assert run.state.outcome == Outcome.INFEASIBLE
        assert run.order_id is None
        assert run.unresolved == ["HARD_CONSTRAINT_UNSATISFIED:ask_with_computed_compromises"]
        blocking = run.state.blocking_violations()
        assert [v.field_path for v in blocking] == ["direction.explicit"]
        assert "12" in blocking[0].evidence["considered"]        # отказ с посчитанным числом


class TestEscalationSteps:
    def test_budget_delta_rebuilds_without_touching_sources(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        calls_before = runtime.cache.source_calls
        tools_before = run.state.budgets.tool_calls

        second = runtime.start("бюджет 160к", state=run.state, now=NOW)
        assert second.changed == {"budget.amount"}
        assert second.dirty == set()                  # tools_for(ступень 0) пуст
        assert second.state.outcome == Outcome.PLAN_READY
        assert second.state.budgets.tool_calls == 0   # переспрашивать источник запрещено
        assert runtime.cache.source_calls == calls_before
        assert second.state.budgets.llm_calls == 3    # parse + объяснение + SL-6 diff
        assert ledger_of(second.state).budget.amount == Decimal("160000")
        assert second.state.budgets.tool_calls == 0 and tools_before == 6

    def test_dates_delta_requeries_only_dirty_tools(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        calls_before = runtime.cache.source_calls

        second = runtime.start("с 20 по 27 ноября", state=run.state, now=NOW)
        assert second.changed == {"dates.start", "dates.end"}
        assert second.dirty == {"transport_search", "hotel_search", "transfer_search"}
        assert second.state.budgets.tool_calls == 3
        assert runtime.cache.source_calls == calls_before + 3
        assert second.state.outcome == Outcome.PLAN_READY
        assert str(second.state.request.dates.start.value) == "2026-11-20"
        assert second.state.chosen.items[0].line_id.endswith("20261120")

    def test_party_delta_recomposes_from_memory(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        calls_before = runtime.cache.source_calls

        second = runtime.start("нас трое с ребёнком", state=run.state, now=NOW)
        assert second.dirty == set()                  # ступень 1: candidates переиспользуются
        assert second.state.budgets.tool_calls == 0
        assert runtime.cache.source_calls == calls_before
        assert second.state.outcome == Outcome.PLAN_READY
        assert int(second.state.request.party.adults.value) == 3
        # номер на четвёрых: вместимость AYT-FAM 4, прежний отель подходит
        assert second.state.chosen.component_refs["hotel"] == "AYT-FAM"


class TestEmptyDestinations:
    def test_no_source_data_gives_a_numbered_refusal(self, runtime: Runtime) -> None:
        engine = Runtime({**SOURCES, "suggest_destinations": lambda args: SearchResult()})
        run = engine.start(MESSAGE, now=NOW)
        run = walk_question(run, engine, "Москва")
        run = walk_question(run, engine, "с 14 по 21")
        assert run.state.outcome == Outcome.INFEASIBLE
        violation = run.state.blocking_violations()[0]
        assert violation.code.value == "CANDIDATES_EMPTY"
        assert violation.expected and violation.actual

    def test_unknown_provenance_is_reported_not_hidden(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert run.state.provenance["transport"] == DataStatus.SYNTHETIC
        assert DataStatus.UNKNOWN not in run.state.provenance.values()


class TestExplainDates:
    """Даты карточки: из плана, без ночёвок — из состояния, и ниоткуда — явной ошибкой.

    Случай не выдуманный: живой контур 2026-10-27 упал внутри `EXPLAIN` на `plan.stays[0]`,
    когда сборка вернула план без ночёвок. `IndexError` посреди демо — это 500 без причины;
    инвариант требует, чтобы нехватка данных была названа.
    """

    def _plan(self, run) -> Plan:
        plan = run.scratch.composition.plan
        assert plan is not None
        return plan

    def test_dates_come_from_the_stays(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        plan = self._plan(run)
        check_in, check_out = runtime._stay_dates(run, plan)
        assert check_in == plan.stays[0].date
        assert check_out == plan.stays[-1].date + timedelta(days=1)

    def test_plan_without_stays_falls_back_to_the_request(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        empty = self._plan(run).model_copy(update={"stays": []})
        check_in, check_out = runtime._stay_dates(run, empty)
        assert check_in == run.state.request.dates.start.value
        assert (check_out - check_in).days == empty.nights

    def test_neither_lodging_nor_dates_is_an_error_not_a_crash(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        empty = self._plan(run).model_copy(update={"stays": []})
        run.state.request.dates.start = FieldValue[date].unknown(kind=ConstraintKind.HARD)
        with pytest.raises(ValueError, match="requires lodging or dates"):
            runtime._stay_dates(run, empty)


class TestZeroNights:
    """Сборка отказывает на нуле ночей, пока не посчитана «совместимость».

    Живой слот №1 выдал start=end из «в ноябре»: ставок в выдаче ровно столько же, сколько
    ночей (ноль), `_hotel_fits` на `len(stays) == nights` считал отель подходящим, и план
    собирался из перелёта и трансфера без единой ночи в отеле, с `violations=0`.
    """

    def test_compose_refuses_before_the_first_hotel(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        state = run.state.model_copy(deep=True)
        state.request.nights = FieldValue[int].known(0, kind=ConstraintKind.HARD, source="dates")
        composition = compose(state, SearchResult(), now=NOW)
        assert composition.plan is None
        violation = composition.blocking[0]
        assert violation.code is ViolationCode.CANDIDATES_EMPTY
        assert "nights=0" in violation.actual

    def test_nights_are_recomputed_when_the_dates_move(self, runtime: Runtime) -> None:
        """Ночи пересчитываются, а не остаются с первого замера.

        Условие «только если unknown» держало 0, посчитанное из `start=end` («в ноябре»),
        даже когда ответ на вопрос про даты поставил 14→21: hard-поле врал, а план выходил
        без ночёвки. Пересчёт разрешён только для нашего же вывода (`source="dates"`) —
        названные словами ночи не перезаписываются (инвариант №1).
        """

        run = golden_run(runtime)
        stale = run.state.request.model_copy(deep=True)
        stale.nights = FieldValue[int].inferred(
            0, kind=ConstraintKind.HARD, source="dates", confidence=1.0
        )
        assert derive_defaults(stale).nights.value == 7

    def test_spoken_nights_survive_the_recompute(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        spoken = run.state.request.model_copy(deep=True)
        spoken.nights = FieldValue[int].known(10, kind=ConstraintKind.HARD, source="user")
        assert derive_defaults(spoken).nights.value == 10


class TestClimateMonth:
    """Климат и подбор направлений спрашиваются по месяцу поездки, а не по месяцу выезда."""

    def test_crossing_stay_pulls_the_month_with_most_nights(self) -> None:
        """«с 28 октября по 5 ноября» → источник зовётся с `dates.month=2026-10`.

        Ночей поровну в двух месяцах, и `stay_month` берёт более ранний — тот, который
        пользователь назвал. Фикстура погоды месяц не читает (климат у неё один на все
        месяцы), поэтому проверяем именно аргумент запроса: он и есть то, что в бою
        выбирает строку в `climate.json`.
        """

        asked: list[str] = []

        def spy(args: Mapping[str, object]) -> SearchResult:
            asked.append(str(args["dates.month"]))
            return weather_climate(args)

        engine = Runtime({**SOURCES, "weather_climate": spy})  # type: ignore[dict-item]
        run = engine.start(MESSAGE, now=NOW)
        run = engine.answer(run, "origin", "Москва", now=NOW)
        run = engine.answer(run, "dates.start", "с 28 октября по 5 ноября", now=NOW)

        assert run.state.request.dates.start.value == date(2026, 10, 28)
        assert run.state.request.dates.end.value == date(2026, 11, 5)
        assert asked and set(asked) == {"2026-10"}


class TestUserVisibleText:
    """Что читает человек: подписи чисел, месяц по-русски и слот №4 после климата.

    Пустая строка в `ref` означает «не показывать», а не «показать пусто», а число обязано
    быть подписано тем, чем оно посчитано (AGENTS.md, «Что видит человек»). Отдельная
    граница — порядок: выбранная до погоды тройка не может объяснить «тёплое море» температурой,
    которой у неё ещё нет.
    """

    def test_question_breaks_sentences_and_names_the_month_in_russian(self, runtime: Runtime) -> None:
        run = runtime.start(MESSAGE, now=NOW)
        #: прежняя склейка давала «…нечего искать в перелётах Варианты: Москва»
        assert ". Варианты: " in run.texts["origin"]
        assert "в перелётах Варианты" not in run.texts["origin"]
        run = walk_question(run, runtime, "Москва")
        #: `dates.month` = «2026-11» — ключ источника; в вопросе он звучит по-русски
        assert "вилку в ноябре" in run.texts["dates.start"]
        assert "11-м месяце" not in run.texts["dates.start"]

    def test_finalist_reason_carries_the_number_it_was_picked_for(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        picked = [candidate for candidate in run.state.candidates.destinations
                  if candidate.ref.get("finalist") == "true"]
        assert len(picked) == 3
        for candidate in picked:
            reason = candidate.ref["why"]
            sea, air = candidate.ref["sea_temp_c"], candidate.ref["day_temp_c"]
            assert sea and air                                # фикстура даёт оба числа
            assert f"море +{sea} °C" in reason                # вода названа водой
            assert "воздух" not in reason                     # и не названа воздух наоборот
            assert f"перелёт {candidate.ref['flight_h']} ч по данным подбора" in reason
            assert "в ноябре" in reason and "2026-11" not in reason

    def test_climate_arrives_before_the_shortlist_without_an_extra_call(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        #: вызовов не стало больше: климат до выбора тройки спрашивается на весь отбор одним
        #: обращением, иначе показанным направлениям нечем было бы объяснить «тёплое море»
        assert run.state.budgets.tool_calls == 6
        filled = [candidate for candidate in run.state.candidates.destinations
                  if candidate.ref.get("day_temp_c")]
        assert len(filled) == len(run.state.candidates.destinations)

    def test_explanation_separates_the_headline_from_the_rest(self, runtime: Runtime) -> None:
        run = golden_run(runtime)
        assert run.explanation.startswith("Турция, Анталья. Заезд ")
        assert "в ноябре" in run.explanation and "2026-11" not in run.explanation
