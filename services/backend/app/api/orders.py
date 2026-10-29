"""Snapshot of a placed order: what the user bought, frozen at checkout time."""

from __future__ import annotations

from decimal import Decimal

from shared.contracts.money import Money
from shared.contracts.state import TripState

from .schemas import OrderLine, OrderSnapshot


def _chosen(state: TripState, component: str, pool: str) -> dict[str, str]:
    ref = state.chosen.component_refs.get(component)
    candidates = getattr(state.candidates, pool)
    return next((c.ref for c in candidates if c.candidate_id == ref), {})


def build_snapshot(state: TripState) -> OrderSnapshot:
    """Только из TripState: история заказов не зависит от того, что потом станет с trip."""
    request = state.request
    destination = _chosen(state, "destination", "destinations")
    party = request.party
    adults = party.adults.value
    groups: dict[str, OrderLine] = {}
    total = Decimal(0)
    currency = state.chosen.items[0].amount.currency if state.chosen.items else state.default_currency
    for line in state.chosen.items:
        kind = line.kind.value
        known = groups.get(kind)
        amount = line.amount.amount + (known.total.amount if known else Decimal(0))
        groups[kind] = OrderLine(kind=kind, count=(known.count if known else 0) + 1,
                                 total=Money(amount=amount, currency=line.amount.currency))
        if line.included_in_budget:
            total += line.amount.amount
    return OrderSnapshot(
        destination=destination.get("name") or request.direction.explicit.value,
        country=destination.get("country") or None,
        date_start=request.dates.start.value,
        date_end=request.dates.end.value,
        nights=request.nights.value,
        guests=None if adults is None else adults + len(party.children.value or []),
        pets=bool(party.pets.value),
        origin=request.origin.value,
        total=Money(amount=total, currency=currency) if state.chosen.items else None,
        lines=list(groups.values()),
    )
