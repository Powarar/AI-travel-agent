"""Policy: severity и repairable кода — свойство кода, а не вывод модели.

Таблица = коды из AGENTS.md. Ни ранжирование, ни repair, ни
review-модель не могут понизить blocking (инвариант №2).
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.contracts.enums import Severity, ViolationCode


@dataclass(frozen=True)
class Policy:
    severity: Severity
    repairable: bool
    actions: tuple[str, ...]


POLICY: dict[ViolationCode, Policy] = {
    ViolationCode.BUDGET_EXCEEDED: Policy(
        Severity.BLOCKING, True, ("resort_by_price", "transport.overnight_ok", "cheaper_hotels")
    ),
    ViolationCode.HARD_CONSTRAINT_UNSATISFIED: Policy(
        Severity.BLOCKING, False, ("ask_with_computed_compromises",)
    ),
    ViolationCode.ARRIVAL_AFTER_CHECKIN_DEADLINE: Policy(
        Severity.BLOCKING, True, ("earlier_flight", "other_transfer", "early_checkin_fee")
    ),
    ViolationCode.CHECKOUT_BEFORE_DEPARTURE: Policy(
        Severity.BLOCKING, True, ("shift_checkout", "later_departure")
    ),
    ViolationCode.TRANSFER_GAP_TOO_SHORT: Policy(
        Severity.BLOCKING, True, ("other_flight", "longer_buffer")
    ),
    ViolationCode.OCCUPANCY_EXCEEDED: Policy(
        Severity.BLOCKING, True, ("family_room", "second_room", "other_hotel")
    ),
    ViolationCode.PET_NOT_ALLOWED: Policy(
        Severity.BLOCKING, True, ("pet_friendly_filter", "pet_transport_rules")
    ),
    ViolationCode.CANDIDATES_EMPTY: Policy(
        Severity.BLOCKING, True, ("escalate_step_2", "then_ask")
    ),
    ViolationCode.UNKNOWN_AVAILABILITY: Policy(Severity.WARNING, True, ("mark_unknown",)),
    ViolationCode.STALE_SOURCE: Policy(Severity.WARNING, False, ("show_checked_at",)),
    ViolationCode.CONFLICTING_SOURCES: Policy(Severity.WARNING, False, ("show_both_prices",)),
    ViolationCode.CONFLICTING_STATE: Policy(Severity.WARNING, True, ("confirm_with_user",)),
    ViolationCode.LEGAL_UNKNOWN: Policy(Severity.WARNING, False, ("no_confirmed_status",)),
    ViolationCode.FABRICATED_FIELD: Policy(Severity.BLOCKING, False,
                                           ("reparse_with_feedback", "recompose_from_candidates")),
    ViolationCode.UNKNOWN_CURRENCY: Policy(Severity.BLOCKING, False, ("rate_source_required",)),
    ViolationCode.DOUBLE_COUNTED: Policy(Severity.BLOCKING, True, ("drop_duplicate_line",)),
}


def policy_for(code: ViolationCode) -> Policy:
    return POLICY[code]
