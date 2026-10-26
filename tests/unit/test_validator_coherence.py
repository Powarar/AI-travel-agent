"""Приёмка T-03: границы стыковок, вместимости, питомцев и наличия ставок."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.agent.app.validation.coherence import has_blocking, validate
from services.agent.app.validation.inputs import HotelSpecs, Plan, StayNight, TransferLeg, TransportLeg
from shared.contracts.enums import (
    BudgetScope,
    ConstraintKind,
    CostScope,
    DataStatus,
    PriceKind,
    ViolationCode,
)
from shared.contracts.money import BudgetLedger, Money, PriceLine
from shared.contracts.request import Budget, FieldValue, Party, Transport, TripRequest
from shared.contracts.state import Candidate, Candidates, TripState

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def money(value: int) -> Money:
    return Money(amount=value, currency="RUB")


def price(line_id: str, value: int, kind: PriceKind = PriceKind.LODGING,
          included: bool = True, status: DataStatus = DataStatus.SYNTHETIC) -> PriceLine:
    return PriceLine(
        line_id=line_id, kind=kind, amount=money(value), scope=CostScope.TOTAL,
        included_in_budget=included, source_id="synthetic_seed", fetched_at=NOW, data_status=status,
    )


def night(day: int, value: int = 5000, availability: bool = True, min_nights: int = 2,
          present: bool = True, status: DataStatus = DataStatus.SYNTHETIC) -> StayNight:
    return StayNight(
        date=date(2026, 11, day), price=price(f"lodging:{day}", value),
        availability=availability, min_nights=min_nights, source_id="synthetic_seed",
        fetched_at=NOW, data_status=status, rate_present=present,
    )


def baseline_plan(**overrides: object) -> Plan:
    plan = Plan(
        destination_code="sochi",
        nights=7,
        adults=2,
        children=[],
        has_pet=False,
        hotel=HotelSpecs(hotel_id="htl-sochi-00", pet_friendly=True, check_in_deadline="23:30",
                         capacity=4, min_nights=2, source_id="synthetic_seed"),
        stays=[night(day) for day in range(1, 8)],
        legs=[
            TransportLeg(leg_id="flt-in", component="transport_in",
                         dep_at=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
                         arr_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
                         duration_h=3.0, direct=True, overnight=False,
                         price=price("transport:in", 12000, PriceKind.TRANSPORT)),
            TransportLeg(leg_id="flt-out", component="transport_out",
                         dep_at=datetime(2026, 11, 8, 20, 0, tzinfo=UTC),
                         arr_at=datetime(2026, 11, 8, 23, 0, tzinfo=UTC),
                         duration_h=3.0, direct=True, overnight=False,
                         price=price("transport:out", 12000, PriceKind.TRANSPORT)),
        ],
        transfers=[
            TransferLeg(transfer_id="tr-arr", kind="arrival", minutes=40, pet_ok=True, capacity=4,
                        price=price("transfer:arr", 3000, PriceKind.TRANSFER)),
            TransferLeg(transfer_id="tr-dep", kind="departure", minutes=40, pet_ok=True, capacity=4,
                        price=price("transfer:dep", 3000, PriceKind.TRANSFER)),
        ],
        departure_buffer_min=90,
        arrival_buffer_min=30,
        now=NOW,
    )
    return plan.model_copy(update=overrides) if overrides else plan


def ledger_of(plan: Plan, budget: int = 200000, scope: BudgetScope = BudgetScope.TOTAL) -> BudgetLedger:
    lines = [stay.price for stay in plan.stays]
    lines += [leg.price for leg in plan.legs if leg.price is not None]
    lines += [t.price for t in plan.transfers if t.price is not None]
    lines += plan.extra_lines
    return BudgetLedger(lines=lines, budget=money(budget), scope=scope)


def codes(plan: Plan, **kw: object) -> set[ViolationCode]:
    ledger = kw.pop("ledger", None) or ledger_of(plan)
    state = kw.pop("state", None)
    return {violation.code for violation in validate(plan, ledger, state)}  # type: ignore[arg-type]


class TestCleanPlan:
    def test_baseline_has_no_violations(self):
        plan = baseline_plan()
        assert validate(plan, ledger_of(plan)) == []

    def test_nights_match_dates(self):
        assert len(baseline_plan().stays) == baseline_plan().nights == 7


class TestTemporalBoundaries:
    def test_arrival_2350_misses_checkin_1500(self):
        plan = baseline_plan(
            legs=[
                TransportLeg(leg_id="flt-in", component="transport_in",
                             dep_at=datetime(2026, 11, 1, 20, 50, tzinfo=UTC),
                             arr_at=datetime(2026, 11, 1, 23, 50, tzinfo=UTC),
                             duration_h=3.0, direct=True, overnight=False,
                             price=price("transport:in", 12000, PriceKind.TRANSPORT))
            ],
        )
        assert ViolationCode.ARRIVAL_AFTER_CHECKIN_DEADLINE in codes(
            plan.model_copy(update={"hotel": plan.hotel.model_copy(update={"check_in_deadline": "15:00"})})
        )

    def test_early_checkin_waiver_clears_violation(self):
        plan = baseline_plan(early_checkin_ok=True,
                             hotel=HotelSpecs(hotel_id="h", pet_friendly=True, check_in_deadline="10:00",
                                              capacity=4, min_nights=2))
        assert ViolationCode.ARRIVAL_AFTER_CHECKIN_DEADLINE not in codes(plan)

    def test_red_eye_departure_stays_same_day(self):
        # прилёт 01:10 8-го, выезд 7-го: checkout(8) == dep date, стыковка допустима
        plan = baseline_plan(
            legs=[
                TransportLeg(leg_id="flt-in", component="transport_in",
                             dep_at=datetime(2026, 10,31, 22, 0, tzinfo=UTC),
                             arr_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
                             duration_h=3.0, direct=True, overnight=True,
                             price=price("transport:in", 9000, PriceKind.TRANSPORT)),
                TransportLeg(leg_id="flt-out", component="transport_out",
                             dep_at=datetime(2026, 11, 8, 23, 55, tzinfo=UTC),
                             arr_at=datetime(2026, 11, 9, 2, 55, tzinfo=UTC),
                             duration_h=3.0, direct=True, overnight=True,
                             price=price("transport:out", 9000, PriceKind.TRANSPORT)),
            ]
        )
        assert ViolationCode.CHECKOUT_BEFORE_DEPARTURE not in codes(plan)

    def test_checkout_after_departure_is_blocking(self):
        plan = baseline_plan(
            legs=[TransportLeg(leg_id="flt-out", component="transport_out",
                               dep_at=datetime(2026, 11, 6, 20, 0, tzinfo=UTC),
                               arr_at=datetime(2026, 11, 6, 23, 0, tzinfo=UTC),
                               duration_h=3.0, direct=True, overnight=False,
                               price=price("transport:out", 12000, PriceKind.TRANSPORT))]
        )
        violations = validate(plan, ledger_of(plan))
        assert ViolationCode.CHECKOUT_BEFORE_DEPARTURE in {v.code for v in violations}
        assert has_blocking(violations)

    def test_checkout_equals_departure_with_night_early_flight(self):
        plan = baseline_plan(
            legs=[TransportLeg(leg_id="flt-out", component="transport_out",
                               dep_at=datetime(2026, 11, 8, 5, 30, tzinfo=UTC),
                               arr_at=datetime(2026, 11, 8, 8, 30, tzinfo=UTC),
                               duration_h=3.0, direct=True, overnight=False,
                               price=price("transport:out", 12000, PriceKind.TRANSPORT))]
        )
        # 05:30 − 90 буфер − 40 трансфер = 03:20 — выехать раньше рассвета
        assert ViolationCode.TRANSFER_GAP_TOO_SHORT in codes(plan)


class TestOccupancyAndPets:
    def test_three_guests_in_two_capacity(self):
        plan = baseline_plan(adults=3, party_total=3)
        assert ViolationCode.OCCUPANCY_EXCEEDED in codes(
            plan.model_copy(update={"hotel": plan.hotel.model_copy(update={"capacity": 2})})
        )

    def test_child_counts_as_guest(self):
        plan = baseline_plan(adults=2, children=[7], party_total=3)
        assert plan.guests == 3
        assert codes(plan) == set()

    def test_hotel_without_pet_friendly(self):
        plan = baseline_plan(has_pet=True)
        assert ViolationCode.PET_NOT_ALLOWED in codes(
            plan.model_copy(update={"hotel": plan.hotel.model_copy(update={"pet_friendly": False})})
        )

    def test_transfer_that_refuses_pet(self):
        plan = baseline_plan(has_pet=True)
        updated = plan.model_copy(
            update={"transfers": [t.model_copy(update={"pet_ok": False}) for t in plan.transfers]}
        )
        found = {v.code: v for v in validate(updated, ledger_of(updated))}
        assert ViolationCode.PET_NOT_ALLOWED in found
        assert found[ViolationCode.PET_NOT_ALLOWED].affected_component == "transfer"


class TestRatesAndData:
    def test_missing_rate_is_status_not_zero_price(self):
        stays = [night(day) for day in range(1, 7)] + [night(7, present=False)]
        plan = baseline_plan(stays=stays)
        violations = {v.code: v for v in validate(plan, ledger_of(plan))}
        assert ViolationCode.UNKNOWN_AVAILABILITY in violations
        assert violations[ViolationCode.UNKNOWN_AVAILABILITY].severity.value == "warning"

    def test_no_availability_is_blocking(self):
        stays = [night(day) for day in range(1, 7)] + [night(7, availability=False)]
        plan = baseline_plan(stays=stays)
        assert has_blocking(validate(plan, ledger_of(plan)))

    def test_min_nights_above_stay_length(self):
        plan = baseline_plan(nights=1, stays=[night(1, min_nights=3)])
        assert ViolationCode.HARD_CONSTRAINT_UNSATISFIED in codes(plan)

    def test_stale_source_is_warning_not_failure(self):
        stays = [night(d, status=DataStatus.STALE) for d in range(1, 8)]
        plan = baseline_plan(stays=stays)
        found = {v.code: v for v in validate(plan, ledger_of(plan))}
        assert found[ViolationCode.STALE_SOURCE].severity.value == "warning"
        assert found[ViolationCode.STALE_SOURCE].repairable is False

    def test_unknown_provenance_stays_unknown(self):
        state = TripState(trip_id=uuid4(), provenance={"hotel:htl-sochi-00": DataStatus.UNKNOWN})
        plan = baseline_plan()
        found = {v.code for v in validate(plan, ledger_of(plan), state)}
        assert ViolationCode.UNKNOWN_AVAILABILITY in found


class TestHardRequirementsAgainstPlan:
    """Инвариант №2 проверяет код: молчаливое отступление от сказанного — blocking."""

    @staticmethod
    def _state(request: TripRequest) -> TripState:
        return TripState(trip_id=uuid4(), request=request)

    def test_pet_said_by_user_but_plan_ignores_it(self):
        request = TripRequest(
            party=Party(pets=FieldValue[bool].known(True, kind=ConstraintKind.HARD))
        )
        found = {v.code: v for v in validate(baseline_plan(), ledger_of(baseline_plan()), self._state(request))}
        assert ViolationCode.HARD_CONSTRAINT_UNSATISFIED in found
        assert found[ViolationCode.HARD_CONSTRAINT_UNSATISFIED].field_path == "party.pets"
        assert found[ViolationCode.HARD_CONSTRAINT_UNSATISFIED].severity.value == "blocking"

    def test_budget_in_ledger_must_be_the_named_one(self):
        request = TripRequest(
            budget=Budget(amount=FieldValue[Money].known(money(150000), kind=ConstraintKind.HARD))
        )
        plan = baseline_plan()
        found = {v.code: v for v in validate(plan, ledger_of(plan, budget=150000), self._state(request))}
        assert ViolationCode.HARD_CONSTRAINT_UNSATISFIED not in found

        swapped = {v.code: v for v in validate(plan, ledger_of(plan, budget=200000), self._state(request))}
        assert swapped[ViolationCode.HARD_CONSTRAINT_UNSATISFIED].field_path == "budget.amount"

    def test_max_hours_direct_rejects_the_long_leg(self):
        request = TripRequest(
            transport=Transport(max_hours_direct=FieldValue[float].known(3.0, kind=ConstraintKind.HARD))
        )
        plan = baseline_plan()
        assert ViolationCode.HARD_CONSTRAINT_UNSATISFIED not in codes(plan, state=self._state(request))

        slow = request.model_copy(update={"transport": Transport(
            max_hours_direct=FieldValue[float].known(2.5, kind=ConstraintKind.HARD))})
        found = [v for v in validate(plan, ledger_of(plan), self._state(slow))
                 if v.code == ViolationCode.HARD_CONSTRAINT_UNSATISFIED]
        assert {v.affected_component for v in found} == {"transport_in", "transport_out"}
        assert found[0].field_path == "transport.max_hours_direct"

    def test_unknown_field_does_not_constrain_the_plan(self):
        state = TripState(
            trip_id=uuid4(),
            candidates=Candidates(hotels=[Candidate(candidate_id="htl-sochi-00", kind="hotel")]),
        )
        assert codes(baseline_plan(), state=state) == set()
