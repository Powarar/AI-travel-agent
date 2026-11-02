"""Индексируемый текст и текст запроса — оба строит код (AGENTS.md).

Отель и запрос пишутся одним словарём: удобства и темы переводятся на русский тем же
способом, иначе «бассейн» из пожелания не встретится с `pool` из каталога.
"""

from __future__ import annotations

import hashlib

from shared.contracts.request import TripRequest

from .hard_filter import HotelCandidate

AMENITY_RU: dict[str, str] = {
    "air_conditioning": "кондиционер",
    "airport_shuttle": "трансфер из аэропорта",
    "beach_first_line": "первая линия пляж море",
    "breakfast": "завтрак",
    "gym": "спортзал фитнес",
    "kids_club": "детский клуб дети",
    "laundry": "прачечная",
    "parking": "парковка",
    "pool": "бассейн",
    "restaurant": "ресторан",
    "spa": "спа",
    "wifi": "wifi интернет",
    "pet_friendly": "можно с животными собака",
}

THEME_RU: dict[str, str] = {
    "beach": "море пляж",
    "city": "город",
    "nature": "природа горы",
    "culture": "культура музеи",
    "food": "еда гастрономия ресторан",
}


def index_text(hotel: HotelCandidate) -> str:
    """Одна строка на объект, без чанков: название, звёзды, место, удобства."""
    parts = [hotel.name]
    if hotel.stars:
        parts.append(f"{hotel.stars} звезды")
    parts.append(hotel.destination_name)
    if hotel.address:
        parts.append(hotel.address)
    parts.extend(AMENITY_RU.get(a, a) for a in sorted(hotel.amenities))
    if hotel.pet_friendly:
        parts.append(AMENITY_RU["pet_friendly"])
    return " ".join(parts)


def pref_text(request: TripRequest) -> str:
    """`" ".join(soft values)` — запрос к эмбеддингам не пишет модель."""
    themes = request.themes.value or []
    return " ".join([*request.soft_preferences, *(THEME_RU[t.value] for t in themes)]).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
