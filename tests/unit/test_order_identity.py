"""Telegram initData signature check and the order snapshot built at checkout."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4

import pytest

from services.backend.app.api.identity import InvalidInitData, verify_init_data
from services.backend.app.api.orders import build_snapshot
from shared.contracts.enums import CostScope, DataStatus, FieldStatus, PriceKind
from shared.contracts.money import Money, PriceLine
from shared.contracts.state import Candidate, TripState

TOKEN = "123456:test-token"


def sign(fields: dict[str, str], token: str = TOKEN) -> str:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def init_data(auth_date: int = 1_700_000_000, user_id: int = 42) -> str:
    return sign({"auth_date": str(auth_date), "query_id": "q1",
                 "user": json.dumps({"id": user_id, "first_name": "Аня"}, ensure_ascii=False)})


def test_valid_init_data_returns_user() -> None:
    assert verify_init_data(init_data(), TOKEN, now=1_700_000_100)["id"] == 42


def test_tampered_or_foreign_init_data_is_rejected() -> None:
    forged = init_data().replace("%22id%22%3A+42", "%22id%22%3A+43")
    with pytest.raises(InvalidInitData):
        verify_init_data(forged, TOKEN, now=1_700_000_100)
    with pytest.raises(InvalidInitData):
        verify_init_data(init_data(), "999:other-bot", now=1_700_000_100)
    with pytest.raises(InvalidInitData):
        verify_init_data("auth_date=1700000000&user=%7B%22id%22%3A1%7D", TOKEN, now=1_700_000_100)


def test_expired_init_data_is_rejected() -> None:
    with pytest.raises(InvalidInitData):
        verify_init_data(init_data(), TOKEN, now=1_700_000_000 + 25 * 3600)


def _line(kind: PriceKind, amount: str, included: bool = True) -> PriceLine:
    return PriceLine(line_id=str(uuid4()), kind=kind, amount=Money(amount=Decimal(amount), currency="RUB"),
                     scope=CostScope.TOTAL, included_in_budget=included, source_id="seed",
                     fetched_at=datetime(2026, 10, 1, tzinfo=UTC), data_status=DataStatus.SYNTHETIC)


def test_snapshot_groups_lines_and_sums_only_budget_items() -> None:
    state = TripState(trip_id=uuid4(), version=3)
    state.candidates.destinations.append(Candidate(candidate_id="d1", kind="destination",
                                                   ref={"name": "Сочи", "country": "Россия"}))
    state.chosen.component_refs["destination"] = "d1"
    state.chosen.items.extend([_line(PriceKind.LODGING, "4000.50"), _line(PriceKind.LODGING, "4000.50"),
                               _line(PriceKind.TRANSPORT, "10000"), _line(PriceKind.FEE, "500", included=False)])
    state.request.dates.start.value = date(2026, 11, 14)
    state.request.dates.start.status = FieldStatus.KNOWN
    state.request.party.adults.value = 2
    state.request.party.children.value = [7]

    snapshot = build_snapshot(state)

    assert (snapshot.destination, snapshot.country, snapshot.guests) == ("Сочи", "Россия", 3)
    assert snapshot.date_start == date(2026, 11, 14)
    assert snapshot.total == Money(amount=Decimal("18001.00"), currency="RUB")
    lodging = next(line for line in snapshot.lines if line.kind == "lodging")
    assert (lodging.count, lodging.total.amount) == (2, Decimal("8001.00"))
    assert {line.kind for line in snapshot.lines} == {"lodging", "transport", "fee"}
