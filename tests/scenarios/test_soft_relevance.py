"""Слот №3 в контуре: мягкая релевантность меняет состав плана и не ломает воспроизводимость.

Три вещи, которые здесь проверяются (CONTRACTS.md, «Мягкая релевантность (слот №3)»):

* ранжирование работает — при выставленном балле `compose` берёт другой отель;
* отказ слота не меняет ничего — офлайн-прогон и прогон с `soft_relevance() -> None`
  дают тот же состав плана, иначе воспроизводимость трейса зависит от доступности сети;
* учёт хода честный — `/rerank` не ест `llm_calls` (лимит шести обращений про чат-слоты),
  но его токены попадают в `budgets.tokens`.

Заодно — два инварианты рядом с слотом №4: числа, которых нет в `ref` кандидата, не попадают
в `TripState` (иначе guard сверяет объяснение по тексту той же модели), и деньги в слот №5
уходят уже отформатированными.
"""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from services.agent.app.llm.provider_openai_compatible import _usage_tokens
from services.agent.app.llm.rerank import RerankHit
from services.agent.app.llm.slots import Explanation, MockSlots
from services.agent.app.orchestration import nodes
from services.agent.app.orchestration.nodes import SearchResult, apply_relevance, merge_results
from services.agent.app.orchestration.runtime import Runtime
from shared.contracts.enums import DataStatus, Theme
from shared.contracts.money import BudgetLedger, Money
from shared.contracts.state import Candidate, TripState
from tests.scenarios.test_golden_path import (
    MESSAGE,
    NOW,
    SOURCES,
    _money,
    golden_run,
    walk_question,
)

UTC = NOW.tzinfo

#: `trip_id` без цифр: haystack guard'а — сериализованное состояние целиком, а в hex
#: произвольного UUID двухзначные числа встречаются примерно в половине случаев, и проверка
#: «выдуманное 29 поймано» на `uuid4()` была бы flaky-тестом.
QUIET_TRIP = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class RankedSlots(MockSlots):
    """Мок с известными баллами: проверяем следствие, а не запрос к шлюзу."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.tokens_used = 0
        self.calls: list[tuple[str, list[str]]] = []

    def soft_relevance(self, query: str, documents: dict[str, str]):
        self.calls.append((query, sorted(documents)))
        self.tokens_used += 120
        return {cid: self.scores[cid] for cid in documents if cid in self.scores}


class UnavailableSlots(RankedSlots):
    """Слот выключен или шлюз молчит: `None` на входе слота №3."""

    def soft_relevance(self, query: str, documents: dict[str, str]):
        self.calls.append((query, sorted(documents)))
        return None


def run_with(scores: dict[str, float]) -> tuple[Runtime, RankedSlots, object]:
    slots = RankedSlots(scores)
    engine = Runtime(SOURCES, slots=slots)  # type: ignore[arg-type]
    run = engine.start(MESSAGE, now=NOW)
    run = walk_question(run, engine, "Москва")
    run = walk_question(run, engine, "с 14 по 21")
    return engine, slots, run


def relevance_of(run) -> dict[str, str]:
    return {candidate.candidate_id: candidate.ref.get("relevance", "")
            for candidate in run.state.candidates.hotels}


def ruble(value: str) -> Decimal:
    """«125 200 ₽» → Decimal: сумма в нарушениях лежит уже отформатированной для человека."""

    return Decimal(re.sub(r"[^\d.]", "", value.replace("\u00a0", " ")))


class TestQueryAndDocument:
    """Запрос и текст кандидата собирает код, а не модель."""

    def test_themes_and_phrases_become_one_russian_line(self) -> None:
        slots = MockSlots()
        request = slots.parse("хочу к тёплому морю в ноябре, без долгих перелётов")
        query = nodes.relevance_query(request)
        assert "море пляж" in query
        assert "без долгих перелётов" in query
        #: английское значение enum в русском запросе — мусор: реранкер скорит лексику
        assert "beach" not in query

    def test_a_genitive_case_does_not_switch_the_paid_slot_off(self) -> None:
        """«отель у моря» обязаны давать запрос: без него `/rerank` не вызывается вовсе.

        Мок T-05 искал «море» и «морю» целыми формами, а падеж «моря» не содержал ни одной —
        `themes` оставались неизвестны, запрос выходил пустым, и runtime пропускал слот №3.
        На платном прогоне это выглядит как «мы заплатили за ноль»: реранк оплачивается только
        когда его вызвали, а план вызовов считается моками на том же входе.
        """

        request = MockSlots().parse("нужен атмосферный отель у моря в сочи с 12 по 17 ноября")
        assert nodes.relevance_query(request).strip() != ""
        #: и падеж, и stem: «у моря», «к морю», «на побережье» — одна тема
        assert request.themes.value == [Theme.BEACH]

    def test_document_carries_amenities_translated(self) -> None:
        candidate = Candidate(
            candidate_id="AYT-LUX", kind="hotel",
            ref={"name": "Gulf Palace", "stars": "5", "destination": "AYT",
                 "amenities": "spa,beach_first_line,unknown_thing"},
            score=0.195, data_status=DataStatus.SYNTHETIC,
        )
        text = nodes.hotel_document(candidate)
        assert text.startswith("Gulf Palace, 5 звёзд, AYT, ")
        assert "спа" in text and "первая линия пляжа" in text
        #: неизвестное значение словаря остаётся как названо, а не выкидывается
        assert "unknown_thing" in text

    def test_empty_amenities_do_not_leave_a_comma(self) -> None:
        candidate = Candidate(candidate_id="X", kind="hotel",
                              ref={"name": "Тихий двор", "stars": "3"},
                              score=0.1, data_status=DataStatus.SYNTHETIC)
        assert nodes.hotel_document(candidate) == "Тихий двор, 3 звёзд"


class TestApplyRelevance:
    def test_step_rounding_and_source_score_untouched(self) -> None:
        state = TripState(trip_id=uuid4())
        state.candidates.hotels = [
            Candidate(candidate_id="A", kind="hotel", ref={"name": "A"}, score=0.5,
                      data_status=DataStatus.SYNTHETIC),
            Candidate(candidate_id="B", kind="hotel", ref={"name": "B"}, score=0.4,
                      data_status=DataStatus.SYNTHETIC),
        ]
        updated = apply_relevance(state, {"A": 0.833, "B": 0.674})
        scores = {c.candidate_id: c.ref["relevance"] for c in updated.candidates.hotels}
        #: нормализация в пределах вызова: максимум набора → 1.00, минимум → 0.05
        assert scores == {"A": "1.00", "B": "0.05"}
        #: `score` источника — отдельное число, слот №3 его не перезаписывает
        assert [c.score for c in updated.candidates.hotels] == [0.5, 0.4]

    def test_live_scale_still_spreads_over_buckets(self) -> None:
        """Регрессия на съеденном сигнале: шкала 0.001–0.02 — не «всем ноль».

        Снято живым прогоном 2026-10-27: `qwen3-reranker-8b` на русских документах отдаёт
        0.0013–0.0192. До нормализации пять таких чисел округлялись в «0.00»/«0.05», и
        оплаченный слот не менял план.
        """

        state = TripState(trip_id=uuid4())
        state.candidates.hotels = [
            Candidate(candidate_id=cid, kind="hotel", ref={"name": cid}, score=0.5,
                      data_status=DataStatus.SYNTHETIC)
            for cid in ("A", "B", "C", "D", "E")
        ]
        updated = apply_relevance(state, {"A": 0.0013, "B": 0.0041, "C": 0.0077,
                                          "D": 0.0124, "E": 0.0192})
        buckets = [c.ref["relevance"] for c in updated.candidates.hotels]
        #: минимум вызова — на границе 0.05, максимум — 1.00, промежуточные — distinct
        assert buckets == ["0.05", "0.20", "0.40", "0.65", "1.00"]
        assert len(set(buckets)) == 5, "округление не должно схлопывать различимый порядок"

    def test_identical_scores_leave_the_choice_to_price(self) -> None:
        state = TripState(trip_id=uuid4())
        state.candidates.hotels = [
            Candidate(candidate_id="A", kind="hotel", ref={"name": "A"}, score=0.5,
                      data_status=DataStatus.SYNTHETIC),
            Candidate(candidate_id="B", kind="hotel", ref={"name": "B"}, score=0.4,
                      data_status=DataStatus.SYNTHETIC),
        ]
        updated = apply_relevance(state, {"A": 0.42, "B": 0.42})
        assert [c.ref["relevance"] for c in updated.candidates.hotels] == ["0.50", "0.50"]

    def test_scored_hotel_outranks_unscored_one(self) -> None:
        """Худший скор вызова — 0.05, а у неоценённого балла нет и ключ берёт за него 0.0."""

        state = TripState(trip_id=uuid4())
        state.candidates.hotels = [
            Candidate(candidate_id="A", kind="hotel", ref={"name": "A"}, score=0.5,
                      data_status=DataStatus.SYNTHETIC),
            Candidate(candidate_id="B", kind="hotel", ref={"name": "B"}, score=0.4,
                      data_status=DataStatus.SYNTHETIC),
            Candidate(candidate_id="C", kind="hotel", ref={"name": "C"}, score=0.3,
                      data_status=DataStatus.SYNTHETIC),
        ]
        updated = apply_relevance(state, {"A": 0.0013, "B": 0.0192})
        buckets = {c.candidate_id: c.ref.get("relevance") for c in updated.candidates.hotels}
        #: A — минимум вызова, и всё равно выше C, которому реранкер не ответил
        assert buckets == {"A": "0.05", "B": "1.00", "C": None}

    def test_unknown_candidate_is_ignored(self) -> None:
        state = TripState(trip_id=uuid4())
        state.candidates.hotels = [
            Candidate(candidate_id="A", kind="hotel", ref={"name": "A"}, score=0.5,
                      data_status=DataStatus.SYNTHETIC)]
        updated = apply_relevance(state, {"NOPE": 0.9})
        assert "relevance" not in updated.candidates.hotels[0].ref


class TestComposeUsesRelevance:
    """Был выбран дешёвый отель, стал — подходящий по теме: меняться должен именно состав."""

    def _composed(self, run, scores: dict[str, float], *, budget: str | None = None,
                  only: list[str] | None = None):
        state = apply_relevance(run.state, scores)
        if budget is not None:
            state = nodes.apply_answer(state, "budget.amount", budget)
        if only is not None:
            #: финалисты только из `only`: проверка «что решает балл внутри одного
            #: направления» не должна зависеть от перебора направлений
            state = state.model_copy(deep=True)
            state.candidates.destinations = [
                candidate for candidate in state.candidates.destinations
                if candidate.candidate_id in only
            ]
        search = merge_results(list(run.scratch.results.values()))
        return nodes.compose(state, search, now=NOW)

    def test_more_relevant_hotel_wins_over_cheaper(self) -> None:
        """Среди влезющих в бюджет atmospheric-отель бьёт дешёвый, даже он дороже."""

        engine, _slots, run = run_with({})
        #: 300к: AYT-LUX (183 300 ₽) и AYT-FAM (125 200 ₽) оба в лимите, решает балл
        composition = self._composed(run, {"AYT-FAM": 0.10, "AYT-LUX": 0.95,
                                          "HKT-ONE": 0.50, "HER-ONE": 0.50}, budget="300к")
        assert composition.plan is not None
        assert composition.plan.hotel.hotel_id == "AYT-LUX"

    def test_soft_score_never_makes_a_feasible_request_infeasible(self) -> None:
        """Баг живого прогона: самый релевантный отель вышел за бюджет, и ход умирал в infeasible.

        Снято 2026-10-27: слот №3 поднял `AYT-LUX` (183 300 ₽ при бюджете 150 000 ₽),
        валидатор дал `BUDGET_EXCEEDED`, а более дешёвый подходящий отель никто не взял —
        то есть soft-поле превратило выполнимый запрос в невыполнимый.
        """

        engine, _slots, run = run_with({})
        composition = self._composed(run, {"AYT-FAM": 0.05, "AYT-LUX": 1.00,
                                          "HKT-ONE": 0.50, "HER-ONE": 0.50})
        assert composition.plan is not None
        assert composition.plan.hotel.hotel_id == "AYT-FAM"
        #: уступка названа: мягкое предпочтение не отбрасывается молча
        assert composition.facts["cheaper_than_relevant"] == "Gulf Palace"

    def test_within_one_destination_the_cheapest_miss_wins_when_nothing_fits(self) -> None:
        """Внутри направления при сверхлимите остаётся самый дешёвый набор: отказ обязан называть ближнюю границу."""

        engine, _slots, run = run_with({})
        composition = self._composed(run, {"AYT-FAM": 0.05, "AYT-LUX": 1.00,
                                          "HKT-ONE": 0.50, "HER-ONE": 0.50},
                                     budget="60к", only=["AYT"])
        assert composition.plan is not None
        #: 1.00 у `AYT-LUX`, но он дороже: в отказ идёт минимальная сумма направления
        assert composition.plan.hotel.hotel_id == "AYT-FAM"
        assert "cheaper_than_relevant" not in composition.facts
        #: направление одно, и оно не влезло: предупреждение с числом превышения обязано
        #: пережить сборку, иначе отказ будет выглядеть как «мы не считали»
        assert [note.code.value for note in composition.notes] == ["BUDGET_EXCEEDED"]
        assert composition.notes[0].is_blocking is False
        assert composition.notes[0].evidence["over_by"] == "65 200 ₽"

    def test_when_no_destination_fits_the_refusal_names_the_closest_one(self) -> None:
        """Ни одно направление не влезло → отказ по самому дешёвому из собранных, а не по первому в списке.

        «Ждали 60 000 ₽, вышло 125 200 ₽» полезнее, чем «вышло 196 100 ₽» про самое
        релевантное направление: валидатор по этому плану считает ещё и `reduce_lodging_to`.
        Остальные направления не исчезают — они уходят предупреждениями со своими числами.
        """

        engine, _slots, run = run_with({})
        composition = self._composed(run, {"AYT-FAM": 0.05, "AYT-LUX": 1.00,
                                          "HKT-ONE": 0.50, "HER-ONE": 0.50}, budget="60к")
        assert composition.plan is not None
        total = sum((line.amount.amount for line in composition.lines), Decimal(0))
        assert composition.plan.hotel.hotel_id == "AYT-FAM"
        assert total == Decimal(125200)  # самый дешёвый из трёх сверхлимитных наборов
        missed = {note.evidence["destination"]: note.actual for note in composition.notes}
        assert set(missed) == {"AYT", "HKT", "HER"}
        assert all(not note.is_blocking for note in composition.notes)
        assert ruble(missed["AYT"]) == total

    def test_an_over_budget_destination_does_not_hide_the_cheap_one(self) -> None:
        """Баг питча: самое релевантное направление не влезло, и прогон врал «плана нет».

        Бюджет 140к на фикстурах: `Пхукет` — 196 100 ₽, `Ираклион` — 131 600 ₽. Раньше ход
        заканчивался `INFEASIBLE` на первом направлении, хотя план существовал; теперь
        собирается второе, а первое остаётся в состоянии предупреждением с числами.
        """

        engine, _slots, run = run_with({})
        composition = self._composed(run, {"AYT-FAM": 0.10, "AYT-LUX": 0.95,
                                          "HKT-ONE": 0.50, "HER-ONE": 0.50},
                                     budget="140к", only=["HKT", "HER"])
        assert composition.plan is not None
        assert composition.plan.destination_code == "HER"
        assert sum((line.amount.amount for line in composition.lines), Decimal(0)) <= Decimal(140000)
        assert [note.evidence["destination"] for note in composition.notes] == ["HKT"]

    def test_equal_relevance_falls_back_to_price(self) -> None:
        engine, _slots, run = run_with({})
        composition = self._composed(run, {"AYT-FAM": 0.80, "AYT-LUX": 0.80,
                                          "HKT-ONE": 0.10, "HER-ONE": 0.10})
        assert composition.plan is not None
        #: при равенстве балла решает стоимость ночи — иначе порядок берётся из шума
        assert composition.plan.hotel.hotel_id == "AYT-FAM"

    def test_garbage_relevance_does_not_break_composition(self) -> None:
        engine, _slots, run = run_with({})
        state = run.state.model_copy(update={}, deep=True)
        state.candidates.hotels[0].ref["relevance"] = "не число"
        composition = nodes.compose(
            state, merge_results(list(run.scratch.results.values())), now=NOW)
        assert composition.plan is not None


class TestRuntimeWiring:
    def test_slot_three_runs_once_and_fills_every_hotel(self) -> None:
        _engine, slots, run = run_with({"AYT-FAM": 0.9, "AYT-LUX": 0.5,
                                        "HKT-ONE": 0.4, "HER-ONE": 0.3})
        assert len(slots.calls) == 1
        query, documents = slots.calls[0]
        assert "море" in query
        assert sorted(documents) == ["AYT-FAM", "AYT-LUX", "AYT-STD", "HER-ONE", "HKT-ONE"]
        #: AYT-STD не получил балл (его нет в `scores`) — он отсеян по вместимости, и в
        #: состоянии у него остаётся пустая релевантность, а не ноль от нас
        #: нормализация вызова {0.9, 0.5, 0.4, 0.3}: 0.5 между 0.3 и 0.9 → 0.35
        assert relevance_of(run)["AYT-LUX"] == "0.35"
        assert run.state.outcome is not None

    def test_rerank_is_not_an_llm_call_but_is_a_cost(self) -> None:
        budgets = run_with({"AYT-FAM": 0.9})[2].state.budgets
        #: parse + 2 вопроса + finalists + explain = 5 чат-обращений, реранк сверху не считается
        assert budgets.llm_calls == 5
        assert budgets.tokens == 120

    def test_unavailable_slot_leaves_the_plan_identical(self) -> None:
        slots = UnavailableSlots({})
        engine = Runtime(SOURCES, slots=slots)  # type: ignore[arg-type]
        run = golden_run_on(engine)
        offline = golden_run(Runtime(SOURCES))
        assert run.state.chosen.component_refs == offline.state.chosen.component_refs
        assert all("relevance" not in candidate.ref for candidate in run.state.candidates.hotels)
        assert slots.calls, "слот №3 вызывается, даже когда нечем скорить"


def golden_run_on(engine: Runtime):
    run = engine.start(MESSAGE, now=NOW)
    run = walk_question(run, engine, "Москва")
    run = walk_question(run, engine, "с 14 по 21")
    return engine.checkout(run, now=NOW)


class TestFinalistReasonKeepsOnlyKnownNumbers:
    """Число в `why` живёт, только если оно уже лежит в `ref` того же кандидата."""

    def _mark(self, reason: str, extra: dict[str, str] | None = None) -> str:
        state = TripState(trip_id=uuid4())
        source = Candidate(
            candidate_id="AYT", kind="destination",
            ref={"name": "Анталья", "country": "Турция", "sea_temp_c": "24", "stars": "",
                 **(extra or {})},
            score=0.92, data_status=DataStatus.SYNTHETIC)
        state.candidates.destinations = [source]
        chosen = source.model_copy(update={"ref": {**source.ref, "why": reason}})
        updated = nodes.mark_finalists(state, [chosen])
        return updated.candidates.destinations[0].ref["why"]

    def test_number_from_the_source_survives(self) -> None:
        assert self._mark("море +24° в ноябре") == "море +24° в ноябре"

    def test_fractional_number_from_the_source_survives(self) -> None:
        #: дробное число источника резалось: токен «31.6» сравнивался с множествами групп
        #: цифр («31», «6») и не находилось — а мок SL-4 на каталоге пишет именно «6.7 ч»
        reason = "Dubai — перелёт 6.7 ч"
        assert self._mark(reason, {"flight_h": "6.7"}) == reason

    def test_split_between_values_does_not_let_a_number_slip(self) -> None:
        #: «24» и «6.7» в одном куске дали бы «246.7», и выдуманное «46» прошло бы проверку
        cleaned = self._mark("46 звёзд", {"flight_h": "6.7"})
        assert "46" not in cleaned

    def test_invented_number_is_cut_out_with_its_unit(self) -> None:
        cleaned = self._mark("море +29°, перелёт 3.5 ч, 5 звёзд")
        assert "29" not in cleaned and "3.5" not in cleaned and "5" not in cleaned
        assert not cleaned.endswith(("+", "°,", "ч", "звёзд"))
        assert "море" in cleaned

    def test_reason_without_numbers_is_untouched(self) -> None:
        assert self._mark("тёплое море и короткий перелёт") == "тёплое море и короткий перелёт"

    def test_state_gives_guard_nothing_the_model_invented(self) -> None:
        #: прямая проверка дыры: объяснение с выдуманным числом после `mark_finalists`
        #: ловится, хотя это число только что было в тексте причины
        state = TripState(trip_id=QUIET_TRIP)
        source = Candidate(candidate_id="AYT", kind="destination",
                           ref={"name": "Анталья", "sea_temp_c": "24"},
                           score=0.92, data_status=DataStatus.SYNTHETIC)
        state.candidates.destinations = [source]
        chosen = source.model_copy(update={"ref": {"why": "море 29°"}})
        state = nodes.mark_finalists(state, [chosen])
        from services.agent.app.orchestration.runtime import unverified_numbers
        ledger = BudgetLedger(lines=[], budget=_money(150000), scope="total")
        assert unverified_numbers("29", state, ledger) == ["29"]


class TestUsageAccounting:
    def test_total_tokens_preferred(self) -> None:
        assert _usage_tokens({"total_tokens": 700, "prompt_tokens": 600,
                              "completion_tokens": 100}) == 700

    def test_parts_sum_when_gateway_omits_total(self) -> None:
        assert _usage_tokens({"prompt_tokens": 610, "completion_tokens": 90}) == 700

    def test_missing_usage_is_not_an_error(self) -> None:
        assert _usage_tokens({}) == 0


class TestExplanationCarriesDisplayStrings:
    def test_money_reaches_the_model_already_formatted(self) -> None:
        ledger = BudgetLedger(
            lines=[], budget=_money(150000), scope="total")
        explanation = Explanation.from_parts(
            headline="Анталья", nights=7, guests=3,
            check_in=NOW.date(), check_out=NOW.date(),
            ledger=ledger, facts={})
        assert explanation.displays["total"] == "0 ₽"
        assert explanation.displays["budget"] == "150 000 ₽"
