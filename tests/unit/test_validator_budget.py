"""Приёмка validation layer: деньги — превышение на 1 ₽, scope, двойной учёт, валюта, конфликт источников."""

from datetime import date, datetime, timezone

from tests.unit.test_validator_coherence import (
    baseline_plan,
    ledger_of,
    money,
    night,
    price,
)
from services.agent.app.validation.coherence import validate
from services.agent.app.validation.inputs import Plan
from shared.contracts.enums import BudgetScope, DataStatus, PriceKind, ViolationCode
from shared.contracts.money import BudgetLedger

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def codes_of(plan: Plan, ledger: BudgetLedger) -> set[ViolationCode]:
    return {violation.code for violation in validate(plan, ledger)}


def total_of(plan: Plan) -> int:
    return int(ledger_of(plan).total.amount)


class TestBudgetThresholds:
    def test_total_is_exact_sum_of_nightly_rates(self):
        plan = baseline_plan()
        # 7 ночей × 5000 + 2 рейса × 12000 + 2 трансфера × 3000
        assert total_of(plan) == 35000 + 24000 + 6000

    def test_one_ruble_over_budget_is_blocking(self):
        plan = baseline_plan()
        total = total_of(plan)
        found = codes_of(plan, ledger_of(plan, budget=total - 1))
        assert ViolationCode.BUDGET_EXCEEDED in found

    def test_exactly_at_budget_passes(self):
        plan = baseline_plan()
        assert codes_of(plan, ledger_of(plan, budget=total_of(plan))) == set()

    def test_violation_carries_numbers_for_explanation(self):
        plan = baseline_plan()
        total = total_of(plan)
        violations = [v for v in validate(plan, ledger_of(plan, budget=total - 1000))
                      if v.code == ViolationCode.BUDGET_EXCEEDED]
        assert violations[0].evidence["over_by"] == "1 000 ₽"
        assert violations[0].suggested_actions

    def test_transfer_excluded_from_budget_still_counted_separately(self):
        base = baseline_plan()
        plan = base.model_copy(update={
            "transfers": [t.model_copy(update={"price": t.price.model_copy(update={"included_in_budget": False})})
                          if t.price else t for t in base.transfers]
        })
        ledger = ledger_of(plan)
        assert ledger.total.amount == money(35000 + 24000).amount
        assert ledger.excluded_total.amount == money(6000).amount
        assert codes_of(plan, ledger) == set()

    def test_over_budget_names_the_hotel_ceiling(self):
        plan = baseline_plan()
        found = [v for v in validate(plan, ledger_of(plan, budget=50000))
                 if v.code == ViolationCode.BUDGET_EXCEEDED]
        # всего 65 000 против 50 000 → перебор 15 000; отель 35 000 → потолок 20 000
        assert found[0].evidence["over_by"] == "15 000 ₽"
        assert found[0].evidence["lodging_now"] == "35 000 ₽"
        assert found[0].evidence["reduce_lodging_to"] == "20 000 ₽"


class TestDoubleCountingAndCurrency:
    def test_duplicate_line_id_detected(self):
        plan = baseline_plan()
        ledger = ledger_of(plan)
        duplicated = BudgetLedger(
            lines=[*ledger.lines, ledger.lines[0]], budget=ledger.budget, scope=ledger.scope
        )
        assert ViolationCode.DOUBLE_COUNTED in codes_of(plan, duplicated)

    def test_foreign_currency_line_blocks_total(self):
        from shared.contracts.money import PriceLine

        plan = baseline_plan(
            extra_lines=[PriceLine(
                line_id="fee:tourist_tax:synthetic_seed", kind=PriceKind.FEE,
                amount=money(100).model_copy(update={"currency": "USD"}), scope="total",
                included_in_budget=True, source_id="rates_api", fetched_at=NOW,
                data_status=DataStatus.VERIFIED,
            )]
        )
        assert ViolationCode.UNKNOWN_CURRENCY in codes_of(plan, ledger_of(plan))

    def test_per_person_budget_is_scaled_by_party(self):
        plan = baseline_plan(adults=2, party_total=2)
        ledger = BudgetLedger(
            lines=[stay.price for stay in plan.stays],
            budget=money(15000),
            scope=BudgetScope.PER_PERSON,
        )
        found = [v for v in validate(plan, ledger) if v.code == ViolationCode.BUDGET_EXCEEDED]
        # 15 000 на двоих = 30 000, а ночи стоят 35 000
        assert found[0].expected == "30 000 ₽"

    def test_per_person_budget_that_fits_after_scaling_is_not_a_violation(self):
        plan = baseline_plan(adults=2, party_total=2)
        ledger = BudgetLedger(
            lines=[stay.price for stay in plan.stays],
            budget=money(20000),
            scope=BudgetScope.PER_PERSON,
        )
        assert codes_of(plan, ledger) == set()

    def test_repair_ceiling_never_becomes_negative(self):
        plan = baseline_plan()
        found = [v for v in validate(plan, ledger_of(plan, budget=1000))
                 if v.code == ViolationCode.BUDGET_EXCEEDED]
        assert found[0].evidence["reduce_lodging_to"] == "0 ₽"

    def test_two_sources_for_one_component_is_warning(self):
        from shared.contracts.money import PriceLine

        conflict = PriceLine(
            line_id="lodging:travelpayouts", kind=PriceKind.LODGING, amount=money(4800),
            scope="total", included_in_budget=False, source_id="travelpayouts",
            fetched_at=NOW, data_status=DataStatus.PARTIALLY_VERIFIED,
        )
        plan = baseline_plan(extra_lines=[conflict])
        found = {v.code: v for v in validate(plan, ledger_of(plan))}
        assert ViolationCode.CONFLICTING_SOURCES in found
        assert found[ViolationCode.CONFLICTING_SOURCES].severity.value == "warning"
