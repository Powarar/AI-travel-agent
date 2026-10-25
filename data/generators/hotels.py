"""Отели: OSM/Overpass когда сеть доступна, иначе детерминированная синтетика.

Важно по происхождению (docs/ARCHITECTURE.md §9): каталог — это Hotel (характеристики
без дат), цены — HotelOffer/stay_rates (дата, цена, доступность). Синтетика помечается
data_status=synthetic; на этой машине Overpass недоступен (зеркала 406/таймаут), поэтому
дефолт — синтетика, anchored на реальные координаты из GeoNames.

Ключи детерминизма: hotel:«dest|idx», rate:«hotel_id|date» — два прогона дают байт-в-байт
одинаковые файлы.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import data.generators.common as common
from data.generators.common import pick, randint, sample, unit, jitter

# Окно демо: даты показа ±14 дней вокруг середины ноября.
WINDOW_START = dt.date(2026, 11, 1)
WINDOW_END = dt.date(2026, 12, 7)

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

NAME_POOL_RU = [
    "Морской берег", "Приморская", "Волна", "Лазурный", "Солнечный пляж", "Аврора",
    "Меридиан", "Гранд-Кристалл", "Тихая гавань", "Коралл", "Янтарь", "Платан",
    "Кипарис", "Ассоль", "Жемчужина", "Ривьера", "Бриз", "Омега", "Акварель",
    "Фламинго", "Пеликан", "Таврида", "Империал", "Ника", "Ореанда", "Сосновый",
    "Роза ветров", "Панорама", "Эллада", "Аквамарин", "Изумруд", "Южная",
    "Гелиос", "Диана", "Негоциант", "Агат", "Визит", "Голубая бездна", "Чайка",
    "Персей",
]
NAME_POOL_INTL = [
    "Sea Pearl Resort", "Blue Lagoon", "Palmar", "Costa Sol", "Ambiance", "Nirvana",
    "Casa Mare", "Palm Bay", "Azure Dream", "Sunset Hill", "Ocean Breeze", "La Vela",
    "Serenity", "Coral Sands", "Mirage", "Aqua Vista", "Belvedere", "Grand Bay",
    "Lagoon Side", "Poseidon", "Rivage", "Del Mar", "Onyx", "Madaya", "Sienna",
    "Amara", "Verona Palace", "Caprice", "Elysium", "Forte Village", "Hilton Garden",
    "Novotel", "Ramada", "Marigold", "Haven Resort", "Al Muntaha", "Zen Residence",
    "Bosphorus View", "Sahara Gate", "Indigo Pearl",
]
AMENITIES = [
    "wifi", "breakfast", "pool", "parking", "spa", "gym", "beach_first_line",
    "airport_shuttle", "kids_club", "restaurant", "laundry", "air_conditioning",
]
STREETS_RU = [
    "Набережная", "Морская", "Светлая", "Курортная", "Центральная", "Мира",
    "Ленинская", "Садовая", "Горная", "Приморская",
]

COUNTRY_PRICE_FACTOR = {
    "RU": 1.0, "GE": 0.85, "AM": 0.75, "AZ": 0.85, "KZ": 0.85, "UZ": 0.7,
    "KG": 0.7, "BY": 0.8, "RS": 1.0, "TR": 1.0, "EG": 0.8, "AE": 1.8,
    "TH": 0.9, "VN": 0.65, "CN": 0.95, "MV": 2.4, "CY": 1.35,
}
STAR_BASE_RUB = {2: 3200, 3: 4800, 4: 7900, 5: 13500}


def _season_factor(theme: str, nov_mean_t: float | None) -> float:
    """Пляж в ноябре: чем теплее — тем дороже. Города почти не зависят от сезона."""
    if theme != "beach" or nov_mean_t is None:
        return 0.95 if theme == "nature" else 1.0
    if nov_mean_t < 12:
        return 0.7
    if nov_mean_t < 18:
        return 0.85
    if nov_mean_t < 24:
        return 1.05
    return 1.3


def _try_overpass(dest: dict[str, Any], limit: int) -> list[dict[str, Any]] | None:
    """Реальные отели из OSM. Возвращает None, если ни одно зеркало не ответило."""
    import urllib.error
    import urllib.parse
    import urllib.request

    radius = 9000 if dest["theme"] in ("beach", "resort", "nature") else 12000
    query = (
        f'[out:json][timeout:8];node["tourism"~"hotel|guest_house"]'
        f'(around:{radius},{dest["lat"]},{dest["lon"]});out body {limit};'
    )
    for url in MIRRORS:
        try:
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"User-Agent": common.USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read())
            els = payload.get("elements", [])
            if len(els) >= 3:
                return els
        except Exception:
            continue
    return None


def _hotel_rows(dest: dict[str, Any], n: int, overpass: list | None,
                season: float) -> tuple[list[dict[str, Any]], int]:
    cc = dest["country_code"]
    pool = NAME_POOL_RU if cc == "RU" else NAME_POOL_INTL
    names = sample(f"names:{dest['code']}", pool, min(n, len(pool)))
    taken: set[str] = set()
    rows: list[dict[str, Any]] = []
    real = 0
    for i in range(n):
        key = f"hotel:{dest['code']}|{i}"
        if overpass and i < len(overpass):
            el = overpass[i]
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("official_name")
            if not name or name in taken:
                continue
            stars_s = (tags.get("stars") or "").strip()
            stars = int(stars_s) if stars_s.isdigit() and 1 <= int(stars_s) <= 5 else None
            hotel_id = f"htl-{dest['code']}-osm{el['id']}"
            real += 1
            data_status, source_id = "real", "osm_overpass"
            lat, lon = el["lat"], el["lon"]
        else:
            stars_pool = [2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 5]
            stars = pick(key + "|stars", stars_pool)
            name = names[i]
            hotel_id = f"htl-{dest['code']}-{i:02d}"
            data_status, source_id = "synthetic", "synthetic_seed"
            # детерминированный jitter вокруг центра города: ±0.03° (~3 км)
            lat = round(dest["lat"] + (unit(key + "|lat") - 0.5) * 0.06, 6)
            lon = round(dest["lon"] + (unit(key + "|lon") - 0.5) * 0.06, 6)
        taken.add(name)
        n_amen = randint(key + "|amen", 3, 7)
        base = round(
            STAR_BASE_RUB[stars or 3] * COUNTRY_PRICE_FACTOR.get(cc, 1.0)
            * season * jitter(key + "|price", 0.20) / 50
        ) * 50
        rows.append({
            "hotel_id": hotel_id,
            "dest_code": dest["code"],
            "name": name,
            "stars": stars,
            "lat": lat,
            "lon": lon,
            "address": (
                f"{pick(key + '|street', STREETS_RU)}, {randint(key + '|house', 1, 120)}"
                if cc == "RU" else f"{dest['name']}, {randint(key + '|house', 1, 120)}"
            ),
            "amenities": sample(key + "|amen", AMENITIES, n_amen),
            "pet_friendly": unit(key + "|pet") < 0.3,
            "min_nights": pick(key + "|minn", [1, 1, 2, 2, 3]),
            "check_in_deadline": pick(key + "|ci", ["14:00", "15:00", "16:00"]),
            "base_price_rub": base,
            "data_status": data_status,
            "source_id": source_id,
        })
    return rows, real


def _stay_rates(hotels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Календарь ставок. Нет строки на ночь = UNKNOWN_AVAILABILITY, поэтому часть
    ночей намеренно пропускаем (~7%), ещё ~3% — явный sold out."""
    rate_rows: list[dict[str, Any]] = []
    date = WINDOW_START
    dates = []
    while date <= WINDOW_END:
        dates.append(date)
        date += dt.timedelta(days=1)
    for h in hotels:
        for d in dates:
            key = f"rate:{h['hotel_id']}|{d.isoformat()}"
            r = unit(key)
            if r < 0.07:
                continue  # нет ставки — статус unknown, не средняя цена
            price = h["base_price_rub"]
            if d.weekday() in (4, 5):
                price *= 1.15  # пт/сб
            price = round(price * jitter(key + "|j", 0.08) / 50) * 50
            sold_out = r > 0.97
            rate_rows.append({
                "hotel_id": h["hotel_id"],
                "dest_code": h["dest_code"],
                "date": d.isoformat(),
                "price_per_night": price,
                "availability": not sold_out,
                "min_nights": h["min_nights"],
                "currency": "RUB",
                "data_status": "synthetic",
                "source_id": "synthetic_seed",
            })
    return rate_rows


def run() -> None:
    common.source("osm_overpass", "OpenStreetMap Overpass API",
                  MIRRORS[0], "real")
    common.source("synthetic_seed", "Детерминированный генератор (hash-синтетика)",
                  "local", "synthetic")
    dests = common.read_seed("destinations.json")
    try:
        nov = common.read_seed("climate_november.json")
    except FileNotFoundError:
        nov = {}

    use_overpass = os.environ.get("GEN_OVERPASS") == "1"
    hotels: list[dict[str, Any]] = []
    real_count = 0
    overpass_used = 0
    for i, d in enumerate(dests):
        n = 40 if d["tier"] == 1 else 18
        els = _try_overpass(d, n) if use_overpass else None
        if els:
            overpass_used += 1
        rows, real = _hotel_rows(d, n, els, _season_factor(d["theme"], nov.get(d["code"])))
        hotels.extend(rows)
        real_count += real
        if (i + 1) % 15 == 0:
            print(f"  отели: {i + 1}/{len(dests)}")

    rates = _stay_rates(hotels)
    print(f"  отелей: {len(hotels)} (real из OSM: {real_count}, на направлениях с OSM: {overpass_used})")
    common.write_seed("hotels.json", hotels)
    common.write_seed("stay_rates.json", rates, compact=True)


if __name__ == "__main__":
    run()
