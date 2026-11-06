"""Russian budget guidance must stay mathematically honest."""

from services.agent.app.orchestration.nodes import refusal_text
from services.agent.app.validation.coherence import make
from shared.contracts.enums import ViolationCode


def test_budget_refusal_says_when_lodging_cannot_fix_the_gap() -> None:
    violation = make(
        ViolationCode.BUDGET_EXCEEDED,
        "plan",
        expected="50 000 ₽",
        actual="144 050 ₽",
        evidence={
            "over_by": "94 050 ₽",
            "lodging_now": "10 350 ₽",
            "reduce_lodging_to": "0 ₽",
            "lodging_can_close_gap": "false",
        },
    )

    text = refusal_text([violation])

    assert "Даже без проживания" in text
    assert "до 0 ₽" not in text
