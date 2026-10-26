"""T-01 приёмка: модели TripRequest/TripState/Money/Violation."""

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.contracts import BudgetLedger, Money, PriceLine, TripRequest, TripState, Violation
from shared.contracts.enums import (
    BudgetInclude,
    BudgetScope,
    ConstraintKind,
    CostScope,
    DataStatus,
    DirectionMode,
    FieldStatus,
    PriceKind,
    Severity,
    Stage,
    ViolationCode,
)
from shared.contracts.request import FieldValue
from shared.contracts.state import RunBudget

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def line(line_id: str, amount: str, kind: PriceKind = PriceKind.LODGING, included: bool = True) -> PriceLine:
    return PriceLine(
        line_id=line_id,
        kind=kind,
        amount=Money(amount=Decimal(amount), currency="RUB"),
        scope=CostScope.TOTAL,
        included_in_budget=included,
        source_id="synthetic_seed",
        fetched_at=NOW,
        data_status=DataStatus.SYNTHETIC,
    )


class TestMoney:
    def test_float_is_rejected_not_coerced(self):
        with pytest.raises(ValidationError):
            Money(amount=19.99, currency="RUB")
        with pytest.raises(ValueError):
            Money(amount=Decimal("100"), currency="RUB") + Money(amount=0.5, currency="RUB")  # type: ignore[arg-type]

    def test_int_and_str_become_exact_decimals(self):
        assert Money(amount=4450, currency="RUB").amount == Decimal(4450)
        assert Money(amount="150000", currency="RUB").amount == Decimal("150000")

    def test_cross_currency_add_is_typed_error(self):
        with pytest.raises(TypeError):
            Money(amount=Decimal(100), currency="RUB") + Money(amount=Decimal(1), currency="USD")

    def test_bad_currency_code(self):
        with pytest.raises(ValidationError):
            Money(amount=Decimal(1), currency="rub")

    def test_display_rounds_half_up_once(self):
        assert Money(amount=Decimal("148900.005"), currency="RUB").as_display() == "148900.01 RUB"
        assert Money(amount=Decimal("148900"), currency="RUB").as_display() == "148900 RUB"


class TestBudgetLedger:
    def test_total_is_sum_of_included_lines(self):
        ledger = BudgetLedger(
            lines=[
                line("lodging-1", "60000"),
                line("transport-1", "58900", PriceKind.TRANSPORT),
                line("transfer-1", "4000", PriceKind.TRANSFER, included=False),
            ],
            budget=Money(amount=Decimal(150000), currency="RUB"),
            scope=BudgetScope.TOTAL,
        )
        assert ledger.total.amount == Decimal("118900.00")
        assert ledger.excluded_total.amount == Decimal("4000.00")
        assert ledger.over_budget is None
        assert ledger.breakdown[PriceKind.TRANSPORT].amount == Decimal("58900")

    def test_double_counted_line_raises(self):
        ledger = BudgetLedger(
            lines=[line("lodging-1", "60000"), line("lodging-1", "60000")],
            budget=Money(amount=Decimal(150000), currency="RUB"),
            scope=BudgetScope.TOTAL,
        )
        with pytest.raises(ValueError, match="duplicate PriceLine"):
            _ = ledger.total

    def test_budget_exceeded_by_one_ruble(self):
        ledger = BudgetLedger(
            lines=[line("lodging-1", "150001")],
            budget=Money(amount=Decimal(150000), currency="RUB"),
            scope=BudgetScope.TOTAL,
        )
        assert ledger.over_budget is not None
        assert ledger.over_budget.amount == Decimal("1.00")

    def test_foreign_currency_line_blocks_total(self):
        bad = PriceLine(
            line_id="t-1",
            kind=PriceKind.TRANSPORT,
            amount=Money(amount=Decimal(100), currency="USD"),
            scope=CostScope.TOTAL,
            included_in_budget=True,
            source_id="s",
            fetched_at=NOW,
            data_status=DataStatus.VERIFIED,
        )
        ledger = BudgetLedger(
            lines=[bad],
            budget=Money(amount=Decimal(150000), currency="RUB"),
            scope=BudgetScope.TOTAL,
        )
        with pytest.raises(TypeError):
            _ = ledger.total


class TestTripRequestFields:
    def test_known_field_requires_source(self):
        with pytest.raises(ValidationError):
            FieldValue(value="Сочи", kind=ConstraintKind.HARD, status=FieldStatus.KNOWN)

    def test_unknown_field_has_no_value(self):
        field = FieldValue.unknown(kind=ConstraintKind.HARD)
        assert field.is_unknown and field.value is None and field.source is None

    def test_defaulted_marks_source_default(self):
        request = TripRequest()
        request.party.adults = FieldValue.defaulted(2, kind=ConstraintKind.HARD)
        assert request.party.adults.status == FieldStatus.DEFAULTED
        assert request.party.adults.source == "default"

    def test_fabricated_source_on_unknown_is_rejected(self):
        with pytest.raises(ValidationError):
            FieldValue[int | None](value=None, kind=ConstraintKind.SOFT, status=FieldStatus.UNKNOWN, source="model_guess")

    def test_budget_amount_rejects_float(self):
        with pytest.raises(ValidationError):
            FieldValue.known(Money(amount=1.5, currency="RUB"), kind=ConstraintKind.HARD)  # type: ignore[arg-type]

    def test_request_paths_match_contract(self):
        paths = dict(TripRequest().iter_fields())
        for expected in (
            "origin",
            "direction.mode",
            "dates.start",
            "party.pets",
            "budget.amount",
            "budget.scope",
            "transport.max_hours_direct",
            "lodging.budget_per_night_max",
            "identity.citizenship",
        ):
            assert expected in paths, expected

    def test_direction_mode_open_roundtrip(self):
        request = TripRequest()
        request.direction.mode = FieldValue[DirectionMode].known(DirectionMode.OPEN, kind=ConstraintKind.HARD)
        restored = TripRequest.model_validate(request.model_dump(mode="json"))
        assert restored.direction.mode.value == DirectionMode.OPEN
        assert restored.direction.mode.status == FieldStatus.KNOWN


class TestViolations:
    def test_violation_carries_all_contract_fields(self):
        violation = Violation(
            code=ViolationCode.BUDGET_EXCEEDED,
            severity=Severity.BLOCKING,
            affected_component="plan",
            expected="150000 RUB",
            actual="162400 RUB",
            repairable=True,
            suggested_actions=["overnight_ok", "cheaper_hotels"],
        )
        assert violation.is_blocking
        assert violation.signature() == "BUDGET_EXCEEDED:plan"

    def test_unknown_enum_code_rejected(self):
        with pytest.raises(ValidationError):
            Violation(
                code="NOT_IN_CONTRACT",  # type: ignore[arg-type]
                severity=Severity.WARNING,
                affected_component="hotel",
                repairable=False,
            )


class TestTripState:
    def test_json_roundtrip_keeps_types(self):
        request = TripRequest()
        request.dates.start = FieldValue.known(date(2026, 11, 14), kind=ConstraintKind.HARD)
        request.budget.amount = FieldValue.known(Money(amount="150000", currency="RUB"), kind=ConstraintKind.HARD)
        state = TripState(
            trip_id=uuid4(),
            version=3,
            request=request,
            stage=Stage.VALIDATE,
            provenance={"hotel:htl-sochi-00": DataStatus.SYNTHETIC},
        )
        restored = TripState.model_validate(state.model_dump(mode="json"))
        assert restored.request.dates.start.value == date(2026, 11, 14)
        assert isinstance(restored.request.budget.amount.value, Money)
        assert restored.request.budget.amount.value.amount == Decimal("150000")
        assert restored.provenance["hotel:htl-sochi-00"] == DataStatus.SYNTHETIC

    def test_versions_are_immutable_copies(self):
        state = TripState(trip_id=uuid4(), version=1)
        bumped = state.next_version()
        bumped.stage = Stage.SEARCH
        assert state.version == 1
        assert state.stage == Stage.PARSE
        assert bumped.version == 2

    def test_run_budget_limits_match_contract(self):
        budgets = RunBudget(tool_calls=12)
        assert budgets.exhausted() == "tool_calls"
        assert RunBudget(wall_clock_ms=19_999).exhausted() is None

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            TripState(trip_id=uuid4(), chat_history=["привет"])  # type: ignore[call-arg]


class TestNoFloatMoney:
    def test_money_module_has_no_float(self):
        source = (Path(__file__).resolve().parents[2] / "shared" / "contracts" / "money.py").read_text()
        assert not re.search(r"\bfloat\b", source)
