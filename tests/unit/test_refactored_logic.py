"""Tests verifying bugfixes and refactored logic across nodes, instructor, and schemas."""
from decimal import Decimal
from uuid import uuid4
import pytest

from services.agent.app.llm.instructor import extract_json
from services.agent.app.llm.schemas import coerce
from services.agent.app.orchestration.nodes import _hours, _money, apply_answer
from shared.contracts.enums import ConstraintKind
from shared.contracts.state import TripState


def test_money_parsing_in_nodes():
    cases = [
        ("150к", Decimal("150000")),
        ("150k", Decimal("150000")),
        ("150 тыс", Decimal("150000")),
        ("150 тысяч", Decimal("150000")),
        ("150 т.р.", Decimal("150000")),
        ("150 000", Decimal("150000")),
        ("150000", Decimal("150000")),
        ("150 000 руб", Decimal("150000")),
        ("150000 ₽", Decimal("150000")),
        ("1.5 млн", Decimal("1500000.0")),
        ("80к", Decimal("80000")),
    ]
    for raw, expected in cases:
        parsed = _money(raw)
        assert parsed is not None, f"Failed to parse {raw!r}"
        assert parsed.amount == expected, f"For {raw!r}, expected {expected}, got {parsed.amount}"
        assert parsed.currency == "RUB"


def test_hours_parsing_in_nodes():
    cases = [
        ("90 минут", 1.5),
        ("120 мин", 2.0),
        ("4 часа", 4.0),
        ("3.5 ч", 3.5),
        ("4", 4.0),
        ("не больше 6 часов", 6.0),
    ]
    for raw, expected in cases:
        parsed = _hours(raw)
        assert parsed == expected, f"For {raw!r}, expected {expected}, got {parsed}"


def test_hours_parsing_in_schemas():
    assert coerce("transport.max_hours_direct", "90 минут", year=2026) == 1.5
    assert coerce("transport.max_hours_direct", "120 мин", year=2026) == 2.0
    assert coerce("transport.max_hours_direct", "4 часа", year=2026) == 4.0


def test_apply_answer_extended_fields():
    state = TripState(trip_id=uuid4())

    # party.pets
    state_no_pets = apply_answer(state, "party.pets", "без собаки")
    assert state_no_pets.request.party.pets.value is False
    assert state_no_pets.request.party.pets.kind == ConstraintKind.HARD

    state_with_pets = apply_answer(state, "party.pets", "с собакой")
    assert state_with_pets.request.party.pets.value is True

    # party.adults
    state_adults = apply_answer(state, "party.adults", "3 взрослых")
    assert state_adults.request.party.adults.value == 3

    # transport.prefer_direct
    state_direct = apply_answer(state, "transport.prefer_direct", "только прямой")
    assert state_direct.request.transport.prefer_direct.value is True


def test_extract_json_with_nested_braces_and_strings():
    # JSON containing closing brace inside string value
    raw = """```json
    {
        "why": "smiling face :}",
        "reason": "here is {another} brace",
        "quote": "text with \\"escaped quotes\\""
    }
    ```"""
    result = extract_json(raw)
    assert result["why"] == "smiling face :}"
    assert result["reason"] == "here is {another} brace"
    assert result["quote"] == 'text with "escaped quotes"'
