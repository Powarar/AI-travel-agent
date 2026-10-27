"""Приёмка T-04: реестр инструментов, `affected()` и кэш по content-hash."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from services.agent.app.orchestration import nodes
from services.agent.app.orchestration.graph import (
    DOWNSTREAM,
    affected,
    entry_step,
    search_tools,
    tools_for,
)
from services.agent.app.tools.registry import REGISTRY, TOOL_NAMES, ToolCache, args_hash
from shared.contracts.enums import (
    ConstraintKind,
    CostScope,
    DataStatus,
    EscalationStep,
    PriceKind,
)
from shared.contracts.money import Money, PriceLine
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import Candidate, TripState

UTC = timezone.utc


def make_state() -> TripState:
    return TripState(trip_id=uuid4())


def test_every_tool_declares_its_policy():
    """Приёмка: `inputs`, `guard`, `cost`, `retries`, `backoff`, `timeout`, `fallback`, `cache_ttl`."""

    for name in TOOL_NAMES:
        tool = REGISTRY[name]
        assert tool.name == name
        assert tool.provides and tool.output
        assert callable(tool.guard)
        assert tool.cost in {"none", "low", "med", "high"}
        assert len(tool.backoff) == tool.retries, name
        if tool.cost == "none":
            continue
        assert tool.inputs, name
        assert tool.timeout_s is not None, name
        assert tool.cache_ttl_s is not None, name
        assert tool.fallback != "none", name


def test_every_declared_input_is_a_real_field_path():
    """Пути полей реестра сходятся с `iter_fields()` — иначе реверс-индекс молча мимо."""

    known = {path for path, _ in make_state().request.iter_fields()}
    for tool in REGISTRY.values():
        derived = {"destinations", "transport.arrival_time", "climate", "hotels",
                   "transfers", "violations", "order", "basket", "dates.month"}
        for path in tool.inputs:
            if path in derived:
                continue
            assert path in known, f"{tool.name}: unknown field {path}"


def test_party_pets_dirty_set():
    """Ступень 2 на «у нас собака»: транспорт, отели, трансфер; направления и погода не тронуты."""

    assert affected({"party.pets"}) == {
        "transport_search", "hotel_search", "transfer_search", *DOWNSTREAM
    }


def test_dates_start_researches_transport_and_hotels():
    assert affected({"dates.start"}) == {
        "transport_search", "hotel_search", "transfer_search", *DOWNSTREAM
    }


def test_dates_start_closes_over_transfers():
    """Замыкание вниз: перелёты пересобраны → время прилёта другое → трансфер тоже грязный."""

    assert "transfer_search" in search_tools({"dates.start"})


def test_named_destination_dirty_transport_and_hotels_via_guard():
    """`direction.explicit` не уходит в запрос к источнику — его читает `guard`.

    «Я еду в Сочи» вместо «не знаю» меняет готовность поиска, поэтому замыкание идёт и по
    `guard_fields`: иначе перелёты и отели остались бы с ответами под «неизвестно куда».
    """

    assert {"transport_search", "hotel_search"} <= affected({"direction.explicit"})


def test_month_shift_pulls_destinations_and_climate():
    """`dates.month` — отдельное поле именно ради этого: сдвиг месяца грязнит весь поиск,
    сдвиг даты внутри месяца — только перелёты и отели."""

    assert affected({"dates.month"}) == {
        "suggest_destinations", "weather_climate", "transport_search",
        "hotel_search", "transfer_search", *DOWNSTREAM
    }
    assert "weather_climate" not in affected({"dates.start"})


def test_resolved_destinations_pull_the_whole_downstream_branch():
    assert affected({"destinations"}) == {
        "weather_climate", "transport_search", "hotel_search", "transfer_search", *DOWNSTREAM
    }


def test_compose_and_validate_are_always_downstream():
    for changed in ({"party.pets"}, {"dates.start"}, {"budget.amount"}, {"origin"}):
        assert DOWNSTREAM <= affected(changed)


def test_local_tools_are_not_marked_dirty():
    """`coherence_check` и `checkout_mock` не ходят в источник — их делает downstream."""

    for changed in ({"dates.start"}, {"party.pets"}, {"basket"}):
        assert not {name for name in REGISTRY if REGISTRY[name].cost == "none"} & affected(changed)


def test_budget_amount_at_step_zero_opens_no_source():
    changed = {"budget.amount"}
    assert entry_step(changed) == EscalationStep.RE_RANK
    assert tools_for(changed, entry_step(changed)) == frozenset()


def test_soft_preferences_rerank_cached_hotels_only():
    """«что-то про бассейн» пересобирает ранк по уже найденным отелям, источник молчит."""

    changed = {"soft_preferences"}
    assert "hotel_search" in affected(changed)
    assert tools_for(changed, entry_step(changed)) == frozenset()


def test_query_side_soft_field_escalates_to_search():
    """`themes` уходит в запрос подбора направлений, поэтому не ступень 0."""

    assert entry_step({"themes"}) == EscalationStep.RE_SEARCH
    assert "suggest_destinations" in tools_for({"themes"}, EscalationStep.RE_SEARCH)


def test_party_change_enters_at_recompose_and_escalates_to_search():
    changed = {"party.adults"}
    assert entry_step(changed) == EscalationStep.RECOMPOSE
    assert tools_for(changed, EscalationStep.RECOMPOSE) == frozenset()
    assert {"transport_search", "hotel_search"} <= tools_for(changed, EscalationStep.RE_SEARCH)


def test_date_change_enters_at_re_search():
    assert entry_step({"dates.start"}) == EscalationStep.RE_SEARCH


def test_field_no_tool_consumes_only_rebuilds_the_plan():
    changed = {"identity.citizenship"}
    assert search_tools(changed) == frozenset()
    assert entry_step(changed) == EscalationStep.RECOMPOSE
    assert affected(changed) == DOWNSTREAM


def test_empty_delta_is_just_downstream():
    assert affected(set()) == DOWNSTREAM
    assert entry_step(set()) == EscalationStep.RE_RANK


def test_repeated_identical_args_do_not_reach_the_source():
    """Приёмка: повторный вызов с идентичными аргументами не идёт в источник."""

    tool = REGISTRY["hotel_search"]
    args = {"destination": "Сочи", "check_in": date(2026, 11, 14), "adults": 2}
    cache = ToolCache()
    calls = 0

    def fetch() -> str:
        nonlocal calls
        calls += 1
        return "отель «Жемчужина»"

    first, hit_on_first = cache.call(tool, args, fetch)
    second, hit_on_second = cache.call(tool, args, fetch)

    assert calls == 1
    assert not hit_on_first and hit_on_second
    assert first == second
    assert (cache.source_calls, cache.cache_hits) == (1, 1)


def test_different_args_are_different_keys():
    tool = REGISTRY["hotel_search"]
    cache = ToolCache()
    base = {"destination": "Сочи", "check_in": date(2026, 11, 14), "adults": 2}
    other = dict(base, adults=3)

    cache.call(tool, base, lambda: "a")
    cache.call(tool, other, lambda: "b")

    assert cache.source_calls == 2
    assert args_hash(tool, base) != args_hash(tool, other)


def test_same_args_under_another_tool_is_not_a_hit():
    cache = ToolCache()
    args = {"nights": 7}

    cache.call(REGISTRY["hotel_search"], args, lambda: "hotels")
    cache.call(REGISTRY["transport_search"], args, lambda: "flights")

    assert cache.source_calls == 2 and cache.cache_hits == 0


def test_ttl_expiry_sends_a_fresh_request():
    tool = REGISTRY["transport_search"]
    assert tool.cache_ttl_s is not None
    args = {"origin": "Москва", "destination": "Сочи"}
    cache = ToolCache()

    cache.call(tool, args, lambda: "утренний", now_s=0.0)
    cache.call(tool, args, lambda: "вечерний", now_s=tool.cache_ttl_s + 1)

    assert cache.source_calls == 2


def test_invalidation_drops_only_dirty_tools():
    cache = ToolCache()
    cache.call(REGISTRY["transport_search"], {"origin": "Москва"}, lambda: "flight")
    cache.call(REGISTRY["weather_climate"], {"month": 11}, lambda: "sun")
    assert cache.size() == 2

    dropped = cache.invalidate(["transport_search"])

    assert dropped == 1
    assert cache.size() == 1
    cache.call(REGISTRY["weather_climate"], {"month": 11}, lambda: "sun")
    assert cache.cache_hits == 1


def test_guards_block_a_tool_until_its_inputs_exist():
    state = make_state()
    assert not REGISTRY["transport_search"].ready(state)

    state.request.origin = FieldValue[str].known("Москва", kind=ConstraintKind.HARD)
    assert not REGISTRY["transport_search"].ready(state)

    state.candidates.destinations = [Candidate(candidate_id="d1", kind="destination")]
    assert REGISTRY["transport_search"].ready(state)
    assert not REGISTRY["hotel_search"].ready(state)

    state.request.dates.start = FieldValue[date].known(date(2026, 11, 14), kind=ConstraintKind.HARD)
    state.request.dates.end = FieldValue[date].known(date(2026, 11, 21), kind=ConstraintKind.HARD)
    assert REGISTRY["hotel_search"].ready(state)
    assert not REGISTRY["transfer_search"].ready(state)


def test_explicit_destination_is_enough_for_climate():
    state = make_state()
    state.request.direction.explicit = FieldValue[str].known(
        "Сочи", kind=ConstraintKind.HARD, source="user"
    )
    assert REGISTRY["weather_climate"].ready(state)


def test_local_tools_wait_for_candidates_not_for_a_source():
    """`coherence_check` и `checkout_mock` готовы на том, что уже лежит в состоянии."""

    state = make_state()
    assert not REGISTRY["coherence_check"].ready(state)

    def candidate(id_: str, kind: str) -> Candidate:
        return Candidate(candidate_id=id_, kind=kind)

    state.candidates.transport = [candidate("t1", "transport")]
    state.candidates.hotels = [candidate("h1", "hotel")]
    assert REGISTRY["coherence_check"].ready(state)

    assert not REGISTRY["checkout_mock"].ready(state)
    state.chosen.items = [
        PriceLine(
            line_id="lodging:1", kind=PriceKind.LODGING,
            amount=Money(amount=Decimal("5000"), currency="RUB"),
            scope=CostScope.TOTAL, included_in_budget=True, source_id="synthetic_seed",
            fetched_at=datetime(2026, 10, 26, 9, 0, tzinfo=UTC),
            data_status=DataStatus.SYNTHETIC,
        )
    ]
    assert REGISTRY["checkout_mock"].ready(state)


def dated(start: date, end: date) -> TripRequest:
    request = TripRequest()
    request.dates.start = FieldValue[date].known(start, kind=ConstraintKind.HARD, source="user")
    request.dates.end = FieldValue[date].known(end, kind=ConstraintKind.HARD, source="user")
    return request


def test_stay_month_is_the_month_the_trip_sleeps_in():
    """`dates.month` — один месяц на всю вилку, и брать надо тот, где больше ночей.

    Источник климата ключуется парой `(dest_code, month)`, а «с 28 октября по 5 ноября»
    ночует в двух месяцах. Месяц выезда давал ноябрьскую погоду октябрьской поездке; при
    равном числе ночей берётся более ранний месяц — его и называет пользователь.
    """

    assert nodes.stay_month(dated(date(2026, 11, 28), date(2026, 12, 5))) == "2026-12"
    assert nodes.stay_month(dated(date(2026, 10, 28), date(2026, 11, 5))) == "2026-10"
    assert nodes.stay_month(dated(date(2026, 11, 14), date(2026, 11, 21))) == "2026-11"
    assert nodes.stay_month(dated(date(2026, 10, 30), date(2026, 11, 6))) == "2026-11"
    assert nodes.stay_month(TripRequest()) == ""


def test_month_shift_without_a_new_departure_month_still_dirty():
    """Сдвиг вилки, не тронувший месяц выезда, всё равно пересобирает климат.

    «28.10–05.11» → «01.11–08.11»: выезд остался в ноябре, а половина поездки переехала в
    другой месяц. Сравнение одного месяца это пропускало, и подбор направлений показывал
    погоду по старым датам.
    """

    before = dated(date(2026, 10, 28), date(2026, 11, 5))
    after = dated(date(2026, 11, 1), date(2026, 11, 8))
    assert "dates.month" in nodes.changed_fields(before, after)
    same_months = dated(date(2026, 10, 29), date(2026, 11, 6))
    assert "dates.month" not in nodes.changed_fields(before, same_months)
