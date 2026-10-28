"""T-08 repair-планировщик: не ослабляет hard и не повторяет действие."""

from services.agent.app.repair import next_action
from shared.contracts.enums import Severity, ViolationCode
from shared.contracts.violation import Violation


def _violation(code: ViolationCode, *, repairable: bool = True) -> Violation:
    return Violation(code=code, severity=Severity.BLOCKING, affected_component="plan", repairable=repairable)


def test_repair_action_is_deterministic_and_deduplicated() -> None:
    violation = _violation(ViolationCode.BUDGET_EXCEEDED)
    action = next_action(violation, set())

    assert action is not None
    assert action.signature == "BUDGET_EXCEEDED:plan"
    assert next_action(violation, {action.signature}) is None


def test_non_repairable_hard_constraint_is_left_for_question() -> None:
    assert next_action(_violation(ViolationCode.HARD_CONSTRAINT_UNSATISFIED, repairable=False), set()) is None
