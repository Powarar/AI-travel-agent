"""Честность исходов на реальном каталоге `data/seeds` (шаг «источник читает каталог»).

Фикстуры золотого пути отвечают на вопрос «сходится ли арифметика». Здесь вопрос другой:
«не нашли» обязано означать «не нашли», а не «в фикстуре нет строчки». Поэтому все прогоны
идут через `run_pipeline.build_sources("seed")` — те же шесть сигнатур поверх живых строк
сида, и ни одного числа, которого в каталоге нет.

Сети нет: слоты — моки T-05, токены нулевые. `wall_clock_ms` — единственное число прогона,
которое не обязано повторяться (его считают часы процесса), и ровно поэтому он вычитается из
проверки воспроизводимости, а не из состояния.

Граница, о которой эти тесты прежде всего: направление, названное пользователем, не имеет
права быть подменённым. Не «мы нашли кое-что получше», а «мы не нашли, и вот числа».
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from scripts import run_pipeline as rp
from services.agent.app.llm.slots import MockSlots
from services.agent.app.orchestration.runtime import Runtime
from services.agent.app.tools.seed_sources import catalog, hotel_search, weather_climate
from shared.contracts.enums import Outcome

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
TRIP = UUID("2b1f8c64-3f0e-4f6a-9d2b-1c8a5e7f0a34")
ANSWERS = {"origin": "Москва"}

#: направления, у которых в `transport_legs.json` нет ни одного сегмента: каталог их знает,
#: лететь туда нечем. Подмена «чем-то похожим» — главный риск сборки на реальном каталоге
NO_FLIGHT_DESTINATIONS = ("геленджик", "минводы", "пятигорск", "феодосия", "севастополь")


def runtime() -> Runtime:
    return Runtime(rp.build_sources("seed"), slots=MockSlots())


def trip(message: str, answers: dict[str, str] | None = None) -> rp.TurnReport:
    """Прогон тем же шагом, что печатается в отчёт: вопросы берёт `awaiting_fields`."""

    report = rp.TurnReport()
    rp.walk(runtime(), message, {**ANSWERS, **(answers or {})},
            now=NOW, ask=lambda path, question: "", report=report)
    return report


def climate(month: str, code: str = "sochi"):
    return weather_climate({"destinations": [code], "dates.month": month}).climate[0]


class TestNamedDestinationIsThePlanDestination:
    def test_sochi_makes_a_plan_without_violations(self) -> None:
        report = trip("хочу в сочи с 12 по 17 ноября вдвоём, 150к")
        assert report.outcome == Outcome.PLAN_READY
        assert report.violations == []
        assert report.components["destination"] == "sochi"
        assert report.invented_numbers == []

    def test_checkout_is_idempotent_by_hash_of_arguments(self) -> None:
        report = trip("хочу в сочи с 12 по 17 ноября вдвоём, 150к")
        assert report.order_id is not None and report.order_id.startswith("ord-seed-")

    def test_direction_is_never_substituted_for_a_warmer_one(self) -> None:
        #: Сочи в ноябре +15.5 °C, Дубай +31.6 — «подменить на потеплее» было бы соблазнительно
        report = trip("хочу в сочи с 12 по 17 ноября вдвоём, 150к")
        assert report.finalists == ["sochi"]


class TestRefusalNamesNumbers:
    def test_city_absent_from_the_catalog_is_refused_with_counters(self) -> None:
        report = trip("хочу в гамбург с 12 по 17 ноября вдвоём, 150к")
        assert report.outcome == Outcome.INFEASIBLE
        assert report.order_id is None
        assert report.components == {}
        assert "направлений в каталоге — 61" in report.explanation_text
        assert "названо — гамбург" in report.explanation_text

    def test_refusal_is_russian_text_not_a_code(self) -> None:
        report = trip("хочу в гамбург с 12 по 17 ноября вдвоём, 150к")
        assert report.explanation_text.startswith("Плана нет.")
        assert "CANDIDATES_EMPTY" not in report.explanation_text

    @pytest.mark.parametrize("code", NO_FLIGHT_DESTINATIONS)
    def test_destination_without_flights_is_not_replaced(self, code: str) -> None:
        report = trip(f"хочу в {code} с 12 по 17 ноября вдвоём, 150к")
        assert report.outcome == Outcome.INFEASIBLE
        #: план чужого направления — тихая подмена hard-поля, её быть не может ни при каком исходе
        assert "destination" not in report.components
        assert report.order_id is None
        assert any(label in report.explanation_text
                   for label in ("направлений в выдаче — 0", "рейсов в выдаче — 0"))

    def test_catalog_spelling_of_the_name_still_refuses(self) -> None:
        #: каталог пишет названия латиницей («Gelendzhik»): пока разбор принимал только
        #: кириллицу, слово терялось, `direction.explicit` оставался пустым, и вместо отказа
        #: пользователь получал план Антальи — ловить было нечему, поля-то нет
        report = trip("хочу в Gelendzhik с 12 по 17 ноября вдвоём, 150к")
        assert report.outcome == Outcome.INFEASIBLE
        assert "destination" not in report.components


class TestCatalogGapsStayGaps:
    def test_sea_temperature_is_unknown_because_the_seed_has_none(self) -> None:
        facts = weather_climate({"destinations": ["sochi", "dubai"],
                                 "dates.month": "2026-11"}).climate
        assert [fact.sea_temp_c for fact in facts] == [None, None]
        assert [fact.day_temp_c for fact in facts] == [Decimal("15.5"), Decimal("31.6")]

    def test_october_and_november_are_different_numbers(self) -> None:
        assert climate("2026-10").day_temp_c == Decimal("20.0")
        assert climate("2026-11").day_temp_c == Decimal("15.5")
        assert climate("2026-10").month == "2026-10"

    def test_ocean_months_after_the_data_window_do_not_fabricate_a_number(self) -> None:
        #: перелёты в сиде только на ноябрь: октябрьский Сочи получает другие температуры,
        #: но план из ноябрьских ставок собран быть не может
        report = trip("хочу в сочи с 12 по 17 октября вдвоём, 150к")
        assert report.outcome == Outcome.INFEASIBLE
        assert "destination" not in report.components

    def test_hotel_capacity_is_absent_so_occupancy_is_not_checked(self) -> None:
        offers = hotel_search({"destinations": ["sochi"], "party.adults": "2"}).hotels
        assert offers
        #: дыра данных, названная честно: `OCCUPANCY_EXCEEDED` на каталоге не сработает никогда
        assert {offer.specs.capacity for offer in offers} == {None}

    def test_finalist_reason_does_not_claim_sea_the_catalog_lacks(self) -> None:
        #: причина слота №4 обязана собираться из `ref`: «тёплое море» про направление, у
        #: которого морской температуры нет, — выдумка, которую guard по числам не видит
        report = trip("хочу к тёплому морю в ноябре вдвоём, 150к")
        reasons = [candidate["ref"]["why"] for candidate in
                   report.state_dump["candidates"]["destinations"] if "why" in candidate["ref"]]
        assert reasons
        assert all("море" not in reason for reason in reasons)
        assert all("перелёт " in reason for reason in reasons)


class TestPartyConstraints:
    def test_pet_travel_gets_a_pet_friendly_hotel_and_its_own_transfer(self) -> None:
        report = trip("еду с собакой в сочи на 2 ночи с 14 по 16 ноября, 80к")
        assert report.outcome == Outcome.PLAN_READY
        hotel = report.components["hotel"]
        row = next(item for item in catalog().hotels if str(item["hotel_id"]) == hotel)
        assert row["pet_friendly"] is True
        #: `transfers.json` = одна строка на отель: чужой трансфер собрал бы план из двух поездок
        assert report.components["transfer_arrival"] == f"trf-{hotel}-arrival"
        assert report.components["transport_in"].startswith("flt-leg-")


class TestReproducibility:
    def _state(self) -> dict[str, object]:
        report = rp.TurnReport()
        engine = runtime()
        run = engine.start("хочу в сочи с 12 по 17 ноября вдвоём, 150к", trip_id=TRIP, now=NOW)
        while run.state.awaiting_fields:
            path = run.state.awaiting_fields[0]
            run = engine.answer(run, path, ANSWERS[path], now=NOW)
        if run.state.outcome == Outcome.PLAN_READY:
            run = engine.checkout(run, now=NOW)
        rp._fill(run, engine, report)
        return report.state_dump

    @staticmethod
    def _stable(state: dict[str, object]) -> str:
        #: часы хода — единственное число, которое не обязано повторяться: его считает
        #: `time.monotonic`, и он обнуляется здесь, а не в состоянии
        budgets = state.get("budgets")
        assert isinstance(budgets, dict) and "wall_clock_ms" in budgets
        budgets["wall_clock_ms"] = 0
        return json.dumps(state, sort_keys=True, ensure_ascii=False)

    def test_repeat_run_differs_only_by_the_clock(self) -> None:
        first, second = self._state(), self._state()
        assert first["trip_id"] == str(TRIP)
        assert self._stable(first) == self._stable(second)
        #: одинаковый заказ: `checkout_mock` идемпотентен по хешу аргументов
        assert first["chosen"]["component_refs"]["order"] == second["chosen"]["component_refs"]["order"]

