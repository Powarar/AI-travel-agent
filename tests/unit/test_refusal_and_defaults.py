"""Живой парсер не всегда называет состав: дефолт ставит код; отказ — обычными словами."""

from __future__ import annotations

from services.agent.app.orchestration import nodes
from shared.contracts.enums import ConstraintKind, FieldStatus, Severity, ViolationCode
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.violation import Violation


def test_unknown_adults_default_to_two_in_code() -> None:
    derived = nodes.derive_defaults(TripRequest())
    assert derived.party.adults.value == 2
    assert derived.party.adults.status == FieldStatus.DEFAULTED


def test_named_party_is_not_overwritten_by_default() -> None:
    request = TripRequest()
    request.party.adults = FieldValue[int].known(3, kind=ConstraintKind.HARD)
    assert nodes.derive_defaults(request).party.adults.value == 3


def _violation(code: ViolationCode, **kw: object) -> Violation:
    return Violation(code=code, severity=Severity.BLOCKING, repairable=True, **kw)  # type: ignore[arg-type]


def test_budget_refusal_reads_like_advice() -> None:
    text = nodes.refusal_text([_violation(
        ViolationCode.BUDGET_EXCEEDED, affected_component="plan", expected="150 000 ₽", actual="166 950 ₽",
        evidence={"over_by": "16 950 ₽", "lodging_now": "32 450 ₽", "reduce_lodging_to": "15 500 ₽"},
    )])
    assert text == ("Не получилось собрать поездку. Самый дешёвый вариант стоит 166 950 ₽ при бюджете "
                    "150 000 ₽ — на 16 950 ₽ дороже. Чтобы уложиться, проживание (32 450 ₽) нужно удешевить "
                    "до 15 500 ₽.")


def test_budget_refusal_never_suggests_negative_lodging() -> None:
    text = nodes.refusal_text([_violation(
        ViolationCode.BUDGET_EXCEEDED, affected_component="plan", expected="10 000 ₽", actual="166 950 ₽",
        evidence={"over_by": "156 950 ₽", "lodging_now": "32 450 ₽", "reduce_lodging_to": "-124 500 ₽"},
    )])
    assert "-124 500" not in text and "Даже без проживания" in text


def test_refusal_hides_source_counters_and_machine_keys() -> None:
    text = nodes.refusal_text([_violation(
        ViolationCode.CANDIDATES_EMPTY, affected_component="transfer",
        expected="трансфер от прилёта к выбранному отелю", actual="ни у одного из 5",
        evidence={"destination": "sochi", "трансферов в выдаче": "30", "debug_key": "x"},
    )])
    assert text == "Не получилось собрать поездку. Трансфер: не нашлось — трансфер от прилёта к выбранному отелю."


def test_unknown_city_is_named_with_catalog_size() -> None:
    text = nodes.refusal_text([_violation(
        ViolationCode.CANDIDATES_EMPTY, affected_component="destination", expected="хотя бы одно направление",
        actual="финалистов нет", evidence={"направлений в каталоге": "61", "направлений названо": "гамбург"},
    )])
    assert "Направления «Гамбург» нет среди 61 направлений" in text
