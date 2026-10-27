"""Validator: детерминированные проверки → структурированные violations.

Никаких LLM-импортов (ADR-003): превышение бюджета на 8% — формальный факт, и модель
с вероятностью подтвердит «всё в порядке». Возвращает только структуру, без оценок в тексте.
"""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from shared.contracts.enums import (
    BudgetScope,
    ConstraintKind,
    DataStatus,
    FieldStatus,
    PriceKind,
    Severity,
    ViolationCode,
)
from shared.contracts.money import BudgetLedger, Money, PriceLine
from shared.contracts.request import TripRequest
from shared.contracts.state import Candidate, TripState
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


# ------------------------------------------------------------- название → код
#
# Словарь и транслитерация, а не модель (CONTRACTS.md, «Названное направление»):
# сворачивание «сочи» в `sochi` обязано быть воспроизводимым, и оно должно совпадать со
# схемой, по которой названы коды сида (`data/seeds/destinations.json`). У модели здесь нет
# права голоса: её ответ нельзя положить в основу blocking-нарушения.

#: 1:1 по алфавиту, без «похожих» сочетаний: «щ» → `shch`, «й» → `y`. Расхождение с
#: каталогом возможно только по начальной «е» (`yevpatoriya`), для неё отдельный вариант.
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
})
#: Падеж дописывает в конец одну-две буквы («Анталь**я**» → «Анталь**ю**», «Турци**я**» →
#: «Турци**ю**», «Геленджик**а**»): снимаем до двух последних символов, не трогая основу.
#: Двух хватает на «-ья», «-ией», «-ов»; длиннее усечение способно свести два разных города
#: к одной основе.
#: Короткие основы («со», «ан») не сравниваются вообще: иначе «на аю» нашло бы совпадение
#: в половине каталога.
_MIN_STEM = 4
#: Длина, с которой допустимо сравнение «одно начинается с другого»: падежные усечения
#: (`antalya` → `antaly`) и одна часть составного кода. Порог восемь, а не шесть: `yuzhno` —
#: начало двух разных кодов (`yuzhno-sakhalinsk`, `yuzhno-kurilsk`), и на пороге шесть
#: «южно-сахалинск» совпадал бы с обоими, то есть проверка возвращала бы два направления
#: вместо одного и не могла бы сказать, где план ошибся.
_MIN_CONTAINMENT = 8


#: Названия, которые в каталоге записаны **не** транслитерацией: `Moscow`, `Istanbul`,
#: `Hurghada`, `nha-trang` — исторические латинские формы. Словарь из 7 строк вместо
#: догадливости: совпадение обязано быть названным, а не угаданным по схожести букв.
_IRREGULAR_LATIN = {
    "москва": "moscow",
    "петербург": "saint-petersburg",
    "стамбул": "istanbul",
    "пафос": "paphos",
    "алания": "alanya",
    "нячанг": "nha-trang",
    "хургада": "hurghada",
}
#: Сколько начал слова сравниваем со словарём: `москве`/`московский` и `петербурге`,
#: `санкт-петербург` должны получить ту же латиницу, что и словарная база. Четыре буквы —
#: это окончание основы, а не «сходство букв»: короче — «ста» притянуло бы «стамбул» к
#: любому «стадиону», длиннее — падеж («Москв**ы**») уже не совпал бы с базой.
_IRREGULAR_PREFIX = 4


def _roman_variants(token: str) -> set[str]:
    """Латиница одного слова: база, йотированная начальная «е», «kh» → «h» и словарь."""

    base = token.translate(_CYRILLIC_TO_LATIN)
    forms = {base}
    #: «е»/«ё»/«ю»/«я» на начале слова дают йотированный вариант (`евпатория` → `yevpatoriya`),
    #: в середине они уже транслитерированы как есть и второй вариант не нужен.
    if base[:1] in {"e", "yo", "yu", "ya"}:
        forms.add(f"y{base}")
    #: `Phuket` в каталоге — «h», а не «kh»: одна замена ловит этот случай и не переносит
    #: «kh» на место «х» внутри слова, где оно уже записано как «kh»
    soft = base.replace("kh", "h")
    if soft != base:
        forms.add(soft)
    #: словарные имена по склоняемой базе: `saint-petersburg` дают и «петербург», и
    #: «петербурге»; составное «санкт-петербург» ловится вторым словом, а не склейкой
    head = token[:_IRREGULAR_PREFIX]
    for key, latin in _IRREGULAR_LATIN.items():
        if head == key[:_IRREGULAR_PREFIX]:
            forms.add(latin)
            #: `nha-trang` в каталоге ещё и склеен (`nhantrang`): дефис на одной стороне не
            #: должен превращать точное совпадение в «не нашли»
            if "-" in latin:
                forms.add(latin.replace("-", ""))
    return forms


def _name_forms(text: str) -> set[str]:
    """Формы сравнения строки: транслитерация, склейка составных имён, падежные усечения.

    Одна функция для обеих сторон — и для слова пользователя («анталью»), и для строк
    каталога (`antalya`, «Турция»): сравнение симметричное, и правка схемы транслитерации
    не разъедает один конец, оставляя другой.
    """

    tokens = [token for token in re.split(r"[^а-яёa-z0-9]+", text.lower()) if token]
    if not tokens:
        return set()
    per_token = [_roman_variants(token) for token in tokens]
    forms: set[str] = set()
    for variants in per_token:
        forms |= variants
    #: составное название сравнивается и по частям, и склейкой: код `da-nang` и слово
    #: «дананг» — одна строка каталога в разной записи
    for combo in itertools.product(*per_token):
        forms.add("".join(combo))
    for value in list(forms):
        for cut in (1, 2):
            stem = value[:-cut]
            if len(stem) >= _MIN_STEM:
                forms.add(stem)
    return forms


def _catalog_forms(candidate: Candidate) -> set[str]:
    """Формы направления из каталога: код, название и страна — что дал источник."""

    forms: set[str] = set()
    for value in (candidate.candidate_id, candidate.ref.get("name", ""),
                  candidate.ref.get("country", "")):
        forms |= _name_forms(value)
    return forms


def _matches(named: set[str], catalog: set[str]) -> bool:
    """Совпало ли названное слово со строкой каталога.

    Кроме точного совпадения допустимо только «одно начинается с другого»: падеж дописывает
    буквы в конец («Анталья» → «анталье» → `antalye`, усечённая основа `antaly` — начало
    `antalya`), и пользователь может назвать одну часть составного кода.
    Вхождение в середину и в конец запрещены намеренно: в `novo**rossiy**sk` основа «россия»
    стоит хвостом, и на свободном совпадении «новороссийск» нашёл бы 28 направлений вместо
    нуля — проверка начала бы пропускать подмену вместо того, чтобы её ловить.
    """

    for left in named:
        for right in catalog:
            if left == right:
                return True
            if len(left) >= _MIN_CONTAINMENT and right.startswith(left):
                return True
            if len(right) >= _MIN_CONTAINMENT and left.startswith(right):
                return True
    return False


def check_direction(request: TripRequest, plan: Plan,
                    candidates: list[Candidate]) -> Violation | None:
    """Названное пользователем направление обязано быть в плане (CONTRACTS.md).

    Разрешено ровно два состояния: код плана совпал с названным словом, или
    `HARD_CONSTRAINT_UNSATISFIED` с `field_path="direction.explicit"`. Отсутствие в
    каталоге — тоже нарушение, а не «проверять нечего»: тогда в `evidence` попадает число
    рассмотренных направлений, и отказ остаётся с числами.
    """

    held = request.direction.explicit
    if (held.status is not FieldStatus.KNOWN or held.source != "user"
            or held.kind is not ConstraintKind.HARD):
        return None
    named_value = (held.value or "").strip()
    if not named_value:
        return None

    named = _name_forms(named_value)
    rows = [_catalog_forms(candidate) for candidate in candidates]
    matched = [candidate.candidate_id for candidate, forms in zip(candidates, rows)
               if _matches(named, forms)]
    #: сверять не с чем (источник направлений ещё не отвечал) — остаётся прямое сравнение
    #: кода плана с названным словом: «сочи» и `sochi` здесь совпадут, «сочи» и `AYT` — нет
    if not matched and not rows:
        matched = [plan.destination_code] if _matches(named, _name_forms(plan.destination_code)) else []
    if plan.destination_code in matched:
        return None

    considered = len(candidates)
    why = (
        f"названное направление не совпало ни с одной из {considered} строк источника"
        if not matched else
        "план собран в другом направлении: подходит "
        f"{', '.join(sorted(matched))}, выбрано {plan.destination_code}"
    )
    return make(
        ViolationCode.HARD_CONSTRAINT_UNSATISFIED, "destination",
        expected=named_value, actual=plan.destination_code,
        field_path="direction.explicit",
        evidence={"why": why, "considered": str(considered),
                  "matched": ", ".join(sorted(matched)) or "нет"},
    )


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
        rows = state.candidates.destinations if isinstance(state, TripState) else []
        substituted = check_direction(request, plan, rows)
        if substituted is not None:
            violations.append(substituted)

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
