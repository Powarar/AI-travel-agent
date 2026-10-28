"""Явная формула ранжирования: нормированные признаки × веса, вывод с разбивкой.

Ранжирование получает уже отфильтрованное SQL множество и только переставляет его:
кандидата, которого нет на входе, нет и на выходе.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from services.backend.app.retrieval.hard_filter import HotelCandidate

from .config import WEIGHTS


@dataclass(frozen=True)
class RankedHotel:
    hotel: HotelCandidate
    score: float
    features: dict[str, float]       # нормированные значения 0..1
    contributions: dict[str, float]  # вес × признак, сумма = score


def _minmax(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _price_feature(candidates: Sequence[HotelCandidate]) -> dict[str, float]:
    # Отношение к самой дешёвой ночи, а не min-max: разница в 3% не должна стоить полного веса.
    # Деньги остаются Decimal до деления; float появляется только у безразмерной доли.
    per_night = {c.hotel_id: c.total_price / c.nights for c in candidates}
    lo = min(per_night.values())
    return {k: float(lo / v) if v > 0 else 1.0 for k, v in per_night.items()}


def rank(
    candidates: Sequence[HotelCandidate],
    similarity: Mapping[str, float] | None = None,
    weights: Mapping[str, float] = WEIGHTS,
) -> list[RankedHotel]:
    if not candidates:
        return []
    ids = {c.hotel_id for c in candidates}
    # Оценка для отеля вне множества отбрасывается: вектор не расширяет выдачу.
    sim = _minmax({k: v for k, v in (similarity or {}).items() if k in ids})
    price = _price_feature(candidates)
    ranked = []
    for c in candidates:
        features = {
            "price": price[c.hotel_id],
            "similarity": sim.get(c.hotel_id, 0.0),
            "stars": (c.stars or 0) / 5,
        }
        contributions = {k: round(weights[k] * v, 4) for k, v in features.items()}
        ranked.append(RankedHotel(c, round(sum(contributions.values()), 4), features, contributions))
    ranked.sort(key=lambda r: (-r.score, r.hotel.total_price, r.hotel.hotel_id))
    return ranked
