"""Выбор следующего repair-действия без модели.

Repair не меняет hard-требования пользователя. Он лишь выбирает безопасную следующую
ступень для уже найденного нарушения и не повторяет ту же подпись. Сам пересчёт и
полная валидация остаются в ``Runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.contracts.enums import EscalationStep, ViolationCode
from shared.contracts.violation import Violation


@dataclass(frozen=True)
class RepairAction:
    """Одна попытка repair, пригодная для trace и ``RepairAttempt``."""

    signature: str
    step: EscalationStep
    affected_components: tuple[str, ...]
    note: str


_ACTIONS: dict[ViolationCode, tuple[EscalationStep, tuple[str, ...], str]] = {
    ViolationCode.BUDGET_EXCEEDED: (
        EscalationStep.RE_RANK, ("lodging", "transport"),
        "пересчитать уже найденные варианты с приоритетом цены",
    ),
    ViolationCode.OCCUPANCY_EXCEEDED: (
        EscalationStep.RECOMPOSE, ("lodging",),
        "пересобрать размещение на новый состав",
    ),
    ViolationCode.PET_NOT_ALLOWED: (
        EscalationStep.RE_SEARCH, ("hotels", "transport", "transfer"),
        "точечно обновить pet-friendly варианты",
    ),
    ViolationCode.ARRIVAL_AFTER_CHECKIN_DEADLINE: (
        EscalationStep.RE_SEARCH, ("transport", "transfer"),
        "точечно обновить стыковку рейса и трансфера",
    ),
    ViolationCode.TRANSFER_GAP_TOO_SHORT: (
        EscalationStep.RE_SEARCH, ("transport", "transfer"),
        "точечно обновить рейс или трансфер",
    ),
    ViolationCode.CHECKOUT_BEFORE_DEPARTURE: (
        EscalationStep.RECOMPOSE, ("lodging", "transport"),
        "пересчитать границы проживания и вылета",
    ),
}


def next_action(violation: Violation, seen: set[str]) -> RepairAction | None:
    """Вернуть следующую допустимую попытку либо ``None``.

    Неремонтируемые нарушения и hard-конфликт не ослабляются: дальше рантайм задаст
    вопрос или вернёт ``infeasible``. Повтор той же подписи запрещён до обращения к
    источнику, поэтому repair не зацикливается на одинаковом действии.
    """

    if not violation.repairable:
        return None
    details = _ACTIONS.get(violation.code)
    if details is None:
        return None
    step, components, note = details
    signature = violation.signature()
    if signature in seen:
        return None
    return RepairAction(signature=signature, step=step, affected_components=components, note=note)
