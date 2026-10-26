"""Validator: детерминированные проверки → структурированные violations.

Никаких LLM-импортов (ADR-003): превышение бюджета на 8% — формальный факт, и модель
с вероятностью подтвердит «всё в порядке». Возвращает только структуру, без оценок в тексте.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from shared.contracts.enums import BudgetScope, DataStatus, PriceKind, Severity, ViolationCode
from shared.contracts.money import BudgetLedger, Money, PriceLine
from shared.contracts.request import TripRequest
from shared.contracts.state import TripState
from shared.contracts.violation import Violation
from services.agent.app.validation.inputs import Plan
from services.agent.app.validation.policy import policy_for


def make(code: ViolationCode, component: str, *, expected: object = None, actual: object = None,
         field_path: str | None = None, evidence: dict[str, str] | None = None) -> Violation:
    pol = policy_for(code)
    return Violation(
        code=code,
        severity=pol.severity,
        affected_component=component,
        field_path=field_path,
        expected=None if expected is None else str(expected),
        actual=None if actual is None else str(actual),
        evidence=evidence or {},
        repairable=pol.repairable,
        suggested_actions=list(pol.actions),
    )


def _clock(plan: Plan) -> datetime:
    if plan.now is not None:
        return plan.now
    if plan.stays:
        return plan.stays[0].fetched_at
    return datetime.now()


def _deadline_datetime(arrival: datetime, deadline: str) -> datetime:
    hour, minute = (int(part) for part in deadline.split(":"))
    return arrival.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _all_price_lines(plan: Plan) -> list[PriceLine]:
    lines: list[PriceLine] = []
    for stay in plan.stays:
        lines.append(stay.price)
    lines += [leg.price for leg in plan.legs if leg.price is not None]
    lines += [t.price for t in plan.transfers if t.price is not None]
    lines += plan.extra_lines
    return lines


def validate(plan: Plan, ledger: BudgetLedger, state: TripRequest | TripState | None = None) -> list[Violation]:
    request = state.request if isinstance(state, TripState) else state
    violations: list[Violation] = []
    now = _clock(plan)

    # --- temporal: стыковки ---------------------------------------------------
    arrival = next((leg for leg in plan.legs if leg.component == "transport_in"), None)
    departure = next((leg for leg in plan.legs if leg.component == "transport_out"), None)
    arrival_transfer = next((t for t in plan.transfers if t.kind == "arrival"), None)
    departure_transfer = next((t for t in plan.transfers if t.kind == "departure"), None)

    if arrival is not None:
        reach = arrival.arr_at + timedelta(
            minutes=plan.arrival_buffer_min + (arrival_transfer.minutes if arrival_transfer else 0)
        )
        latest_checkin = _deadline_datetime(arrival.arr_at, plan.hotel.check_in_deadline)
        if reach > latest_checkin and not plan.early_checkin_ok:
            violations.append(
                make(
                    ViolationCode.ARRIVAL_AFTER_CHECKIN_DEADLINE,
                    "hotel",
                    expected=f"до {plan.hotel.check_in_deadline} + buffers",
                    actual=reach.isoformat(timespec="minutes"),
                    evidence={"arrived_at": arrival.arr_at.isoformat(timespec="minutes")},
                )
            )

    last_night = plan.stays[-1].date if plan.stays else None
    if departure is not None and last_night is not None:
        checkout = last_night + timedelta(days=1)
        if checkout > departure.dep_at.date():
            violations.append(
                make(
                    ViolationCode.CHECKOUT_BEFORE_DEPARTURE,
                    "plan",
                    expected=f"выезд ≤ {departure.dep_at.date()}",
                    actual=str(checkout),
                )
            )
        if checkout == departure.dep_at.date() and departure_transfer is not None:
            needed = departure.dep_at - timedelta(minutes=plan.departure_buffer_min + departure_transfer.minutes)
            if needed.hour < 5:
                violations.append(
                    make(
                        ViolationCode.TRANSFER_GAP_TOO_SHORT,
                        "transfer",
                        expected="выезд не раньше 05:00 при таком буфере",
                        actual=needed.isoformat(timespec="minutes"),
                    )
                )

    # --- вместимость и питомцы ------------------------------------------------
    if plan.hotel.capacity is not None and plan.guests > plan.hotel.capacity:
        violations.append(
            make(ViolationCode.OCCUPANCY_EXCEEDED, "hotel",
                 expected=f"≤ {plan.hotel.capacity}", actual=plan.guests)
        )
    if plan.has_pet and not plan.hotel.pet_friendly:
        violations.append(make(ViolationCode.PET_NOT_ALLOWED, "hotel", expected="pet_friendly", actual=False))
    for transfer in plan.transfers:
        if plan.has_pet and transfer.pet_ok is False:
            violations.append(
                make(ViolationCode.PET_NOT_ALLOWED, "transfer", expected="pet_ok", actual=False,
                     evidence={"transfer_id": transfer.transfer_id})
            )
    for leg in plan.legs:
        if plan.has_pet and leg.price is not None and leg.price.data_status == DataStatus.UNKNOWN:
            violations.append(
                make(ViolationCode.UNKNOWN_AVAILABILITY, "transport",
                     evidence={"leg_id": leg.leg_id, "why": "pet transport rules unverified"})
            )

    # --- календарь ставок: отсутствие ставки это статус, а не ноль ------------
    if plan.stays:
        covered = {stay.date for stay in plan.stays if stay.rate_present}
        expected_days = {
            plan.stays[0].date + timedelta(days=offset) for offset in range(plan.nights)
        }
        for missing in sorted(expected_days - covered):
            violations.append(
                make(ViolationCode.UNKNOWN_AVAILABILITY, "hotel",
                     expected=f"ставка на {missing}", actual="нет строки", field_path="stay_rates")
            )
        for stay in plan.stays:
            if not stay.availability:
                violations.append(
                    make(ViolationCode.CANDIDATES_EMPTY, "hotel",
                         expected="availability", actual=f"нет мест на {stay.date}",
                         evidence={"hotel_id": plan.hotel.hotel_id})
                )
            if stay.min_nights > plan.nights:
                violations.append(
                    make(ViolationCode.HARD_CONSTRAINT_UNSATISFIED, "hotel",
                         expected=f"min_nights ≤ {plan.nights}", actual=stay.min_nights,
                         evidence={"on_date": str(stay.date)})
                )
    if len(plan.stays) != plan.nights:
        violations.append(
            make(ViolationCode.UNKNOWN_AVAILABILITY, "hotel",
                 expected=f"{plan.nights} ночей", actual=f"{len(plan.stays)} ставок")
        )

    # --- данные: свежесть и происхождение ------------------------------------
    for stay in plan.stays:
        if stay.data_status == DataStatus.STALE or (now - stay.fetched_at).days > plan.stale_after_days:
            violations.append(
                make(ViolationCode.STALE_SOURCE, "hotel", expected=f"проверка ≤ {plan.stale_after_days} дн.",
                     actual=f"{(now - stay.fetched_at).days} дн. назад",
                     evidence={"source_id": stay.source_id, "on_date": str(stay.date)})
            )
    components: dict[str, set[str]] = {}
    for line in _all_price_lines(plan):
        # line_id = «компонент:объект»; источник берём из поля, а не из идентификатора
        component = line.line_id.split(":")[0]
        components.setdefault(component, set()).add(line.source_id)

    # --- деньги: сумма, валюта, двойной учёт, scope ---------------------------
    try:
        total = ledger.total
    except ValueError as exc:
        violations.append(make(ViolationCode.DOUBLE_COUNTED, "plan", actual=str(exc)))
        total = None
    except TypeError as exc:
        violations.append(make(ViolationCode.UNKNOWN_CURRENCY, "plan", actual=str(exc)))
        total = None

    if total is not None:
        # per_person: сравниваем с бюджетом, умноженным на состав, а не с суммой на человека
        budget_for_plan = ledger.budget
        if ledger.scope == BudgetScope.PER_PERSON:
            party = plan.party_total if plan.party_total is not None else plan.guests
            budget_for_plan = ledger.budget.times(party)

        if total > budget_for_plan:
            over = total - budget_for_plan
            lodging = Money(
                amount=sum((line.amount.amount for line in _all_price_lines(plan)
                            if line.kind == PriceKind.LODGING and line.included_in_budget),
                           Decimal(0)),
                currency=ledger.budget.currency,
            )
            violations.append(
                make(ViolationCode.BUDGET_EXCEEDED, "plan",
                     expected=budget_for_plan.as_display(), actual=total.as_display(),
                     evidence={"over_by": over.as_display(),
                               "lodging_now": lodging.as_display(),
                               "reduce_lodging_to": (lodging - over).as_display()})
            )

    # --- инвариант №2: план не может молча отступать от hard-полей -------------
    if request is not None:
        if request.party.pets.value is True and not plan.has_pet:
            violations.append(
                make(ViolationCode.HARD_CONSTRAINT_UNSATISFIED, "hotel",
                     expected="pet_friendly", actual="план без pet_friendly",
                     field_path="party.pets")
            )

        claimed_budget = request.budget.amount
        if not claimed_budget.is_unknown and claimed_budget.value is not None:
            if claimed_budget.value != ledger.budget:
                violations.append(
                    make(ViolationCode.HARD_CONSTRAINT_UNSATISFIED, "budget",
                         expected=claimed_budget.value.as_display(),
                         actual=ledger.budget.as_display(),
                         field_path="budget.amount",
                         evidence={"why": "замер считается от названного бюджета, не от подменённого"})
                )

        max_hours = request.transport.max_hours_direct
        if not max_hours.is_unknown and max_hours.value is not None:
            for leg in plan.legs:
                if leg.duration_h > max_hours.value:
                    violations.append(
                        make(ViolationCode.HARD_CONSTRAINT_UNSATISFIED, leg.component,
                             expected=f"≤ {max_hours.value} ч в дороге",
                             actual=f"{leg.duration_h} ч",
                             field_path="transport.max_hours_direct",
                             evidence={"leg_id": leg.leg_id})
                    )

    # --- целостность: компонент без provenance = выдуманные данные -------------
    if isinstance(state, TripState) and plan.stays:
        offered = {candidate.candidate_id for candidate in state.candidates.hotels}
        if plan.hotel.hotel_id not in offered:
            violations.append(
                make(ViolationCode.FABRICATED_FIELD, "hotel",
                     expected="отель взят из candidates с provenance",
                     actual=plan.hotel.hotel_id,
                     evidence={"offered": ", ".join(sorted(offered)) or "пусто"})
            )
    if isinstance(state, TripState):
        for key, status in state.provenance.items():
            if status == DataStatus.UNKNOWN:
                violations.append(
                    make(ViolationCode.UNKNOWN_AVAILABILITY, key, actual="unknown",
                         evidence={"why": "статус не становится подтверждённым по умолчанию"})
                )

    # --- конфликт источников: два source_id на один компонент -----------------
    for component, source_ids in components.items():
        if len(source_ids) > 1:
            violations.append(
                make(ViolationCode.CONFLICTING_SOURCES, component,
                     actual=", ".join(sorted(source_ids)),
                     evidence={"why": "обе цены показываются пользователю"})
            )

    return violations


def has_blocking(violations: list[Violation]) -> bool:
    return any(violation.severity == Severity.BLOCKING for violation in violations)
