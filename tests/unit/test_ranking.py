"""retrieval layer: индексируемый текст, кэш эмбеддингов по content_hash и формула ранжирования."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.backend.app.ranking import WEIGHTS, rank
from services.backend.app.retrieval import (
    EmbeddingIndex,
    HardFilter,
    HashingEmbedder,
    HotelCandidate,
    content_hash,
    index_text,
    pref_text,
)
from shared.contracts.enums import ConstraintKind, Theme
from shared.contracts.request import FieldValue, TripRequest


def _hotel(hid: str, price: int, stars: int = 4, amenities: tuple[str, ...] = ()) -> HotelCandidate:
    return HotelCandidate(
        hotel_id=hid, dest_code="sochi", name=hid, stars=stars, address=None, amenities=amenities,
        pet_friendly=False, nights=2, total_price=Decimal(price), max_night_price=Decimal(price) / 2,
        currency="RUB", data_status="synthetic", destination_name="Sochi", transfer_capacity=4,
    )


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return super().embed(texts)


def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_breakdown_sums_to_score_and_price_orders_ties() -> None:
    ranked = rank([_hotel("a", 10000), _hotel("b", 6000), _hotel("c", 8000)])
    assert [r.hotel.hotel_id for r in ranked] == ["b", "c", "a"]
    for r in ranked:
        assert sum(r.contributions.values()) == pytest.approx(r.score, abs=1e-3)
        assert set(r.features) == set(WEIGHTS)
        assert all(0.0 <= v <= 1.0 for v in r.features.values())


def test_similarity_reorders_but_never_adds() -> None:
    ranked = rank([_hotel("a", 6000), _hotel("b", 6200)], {"b": 0.9, "a": 0.1, "ghost": 1.0})
    assert [r.hotel.hotel_id for r in ranked] == ["b", "a"]


def test_index_text_is_one_function_and_hash_is_stable() -> None:
    hotel = _hotel("Аквамарин", 9000, amenities=("pool", "kids_club"))
    text = index_text(hotel)
    assert "бассейн" in text and "детский клуб" in text and "Sochi" in text
    assert content_hash(text) == content_hash(index_text(hotel))


def test_reembedding_happens_only_on_text_change() -> None:
    embedder = CountingEmbedder()
    index = EmbeddingIndex(embedder)
    a, b = _hotel("a", 1, amenities=("pool",)), _hotel("b", 1, amenities=("spa",))
    index.upsert([("a", index_text(a)), ("b", index_text(b))])
    index.upsert([("a", index_text(a)), ("b", index_text(b))])
    assert index.computed == 2
    changed = _hotel("a", 1, amenities=("pool", "spa"))
    index.upsert([("a", index_text(changed))])
    assert index.computed == 3


def test_pref_text_is_built_by_code() -> None:
    request = TripRequest(
        soft_preferences=["тихо", "с бассейном"],
        themes=FieldValue.known([Theme.BEACH], kind=ConstraintKind.SOFT),
    )
    assert pref_text(request) == "тихо с бассейном море пляж"
    index = EmbeddingIndex(HashingEmbedder())
    pool, gym = _hotel("pool", 1, amenities=("pool",)), _hotel("gym", 1, amenities=("gym",))
    index.upsert([("pool", index_text(pool)), ("gym", index_text(gym))])
    sim = index.similarity(pref_text(request), ["pool", "gym"])
    assert sim["pool"] > sim["gym"]


def test_filter_from_request_counts_children_as_guests() -> None:
    request = TripRequest()
    request.dates.start = FieldValue.known(date(2026, 11, 14), kind=ConstraintKind.HARD)
    request.dates.end = FieldValue.known(date(2026, 11, 21), kind=ConstraintKind.HARD)
    request.party.adults = FieldValue.known(2, kind=ConstraintKind.HARD)
    request.party.children = FieldValue.known([7], kind=ConstraintKind.HARD)
    f = HardFilter.from_request(request)
    assert f.guests == 3 and f.nights == 7
