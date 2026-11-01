"""Направления: GeoNames bulk (cities500.zip, без ключа) → реальные координаты, население, TZ.

Кураторский список ~50 туристических направлений: юг РФ, Крым, острова (Сахалин/Курилы/
Камчатка), Кавказ, Средняя Азия и популярные пляльные направления из Москвы.
"""
from __future__ import annotations

from typing import Any

import data.generators.common as common
from data.generators.common import http_get

GEONAMES_URL = "https://download.geonames.org/export/dump/cities500.zip"
COUNTRIES = {
    "RU": "Россия", "GE": "Грузия", "AM": "Армения", "AZ": "Азербайджан",
    "KZ": "Казахстан", "UZ": "Узбекистан", "KG": "Кыргызстан", "BY": "Беларусь",
    "RS": "Сербия", "TR": "Турция", "EG": "Египет", "AE": "ОАЭ", "TH": "Таиланд",
    "VN": "Вьетнам", "CN": "Китай", "MV": "Мальдивы", "CY": "Кипр", "UA": "Украина",
}

# GeoNames кодирует Крым как UA; для продукта это российские направления.
CRIMEA_DISPLAY = {
    "yalta", "sevastopol", "alushta", "sudak", "feodosiya", "yevpatoriya",
}

# (code, строка-запрос в GeoNames, expected country|None, тема, tier)
# tier=1 → плотнее каталог отелей; expected=None для спорных территорий (Абхазия).
CURATED: list[tuple[str, str, str | None, str, int]] = [
    # юг России и Крым
    ("sochi", "Sochi", "RU", "beach", 1),
    ("anapa", "Anapa", "RU", "beach", 2),
    ("gelendzhik", "Gelendzhik", "RU", "beach", 2),
    ("yalta", "Yalta", "UA", "beach", 1),
    ("sevastopol", "Sevastopol", "UA", "city", 2),
    ("alushta", "Alushta", "UA", "beach", 2),
    ("sudak", "Sudak", "UA", "beach", 2),
    ("feodosiya", "Feodosiya", "UA", "beach", 2),
    ("yevpatoriya", "Yevpatoriya", "UA", "beach", 2),
    ("kislovodsk", "Kislovodsk", "RU", "resort", 2),
    ("pyatigorsk", "Pyatigorsk", "RU", "resort", 2),
    ("minvody", "Mineralnye Vody", "RU", "resort", 2),
    ("belokurikha", "Belokurikha", "RU", "nature", 2),
    # крупные города РФ
    ("moscow", "Moscow", "RU", "city", 1),
    ("saint-petersburg", "Saint Petersburg", "RU", "city", 1),
    ("kaliningrad", "Kaliningrad", "RU", "city", 2),
    ("kazan", "Kazan", "RU", "city", 2),
    ("nizhny-novgorod", "Nizhny Novgorod", "RU", "city", 2),
    ("ekaterinburg", "Yekaterinburg", "RU", "city", 2),
    ("vladivostok", "Vladivostok", "RU", "city", 2),
    # острова и дальневосточные курорты
    ("yuzhno-sakhalinsk", "Yuzhno-Sakhalinsk", "RU", "nature", 2),
    ("kurilsk", "Kurilsk", "RU", "nature", 2),
    ("yuzhno-kurilsk", "Yuzhno-Kurilsk", "RU", "nature", 2),
    ("petropavlovsk", "Petropavlovsk-Kamchatsky", "RU", "nature", 2),
    ("ulan-ude", "Ulan-Ude", "RU", "nature", 2),
    ("irkutsk", "Irkutsk", "RU", "nature", 2),
    ("gorno-altaysk", "Gorno-Altaysk", "RU", "nature", 2),
    ("makhachkala", "Makhachkala", "RU", "beach", 2),
    # Абхазия (в GeoNames CountryCode=GE, город ищем без привязки к стране)
    ("sukhumi", "Sukhumi", None, "beach", 2),
    ("gagra", "Gagra", None, "beach", 2),
    # Закавказье и Центральная Азия
    ("tbilisi", "Tbilisi", "GE", "city", 2),
    ("batumi", "Batumi", "GE", "beach", 2),
    ("yerevan", "Yerevan", "AM", "city", 2),
    ("baku", "Baku", "AZ", "city", 2),
    ("almaty", "Almaty", "KZ", "city", 2),
    ("bishkek", "Bishkek", "KG", "nature", 2),
    ("cholpon-ata", "Cholpon-Ata", "KG", "beach", 2),
    ("samarkand", "Samarkand", "UZ", "city", 2),
    ("bukhara", "Bukhara", "UZ", "city", 2),
    ("tashkent", "Tashkent", "UZ", "city", 2),
    # Беларусь / Сербия
    ("minsk", "Minsk", "BY", "city", 2),
    ("belgrade", "Belgrade", "RS", "city", 2),
    # Турция
    ("antalya", "Antalya", "TR", "beach", 1),
    ("alanya", "Alanya", "TR", "beach", 2),
    ("kemer", "Kemer", "TR", "beach", 2),
    ("bodrum", "Bodrum", "TR", "beach", 2),
    ("marmaris", "Marmaris", "TR", "beach", 2),
    ("istanbul", "Istanbul", "TR", "city", 2),
    # Египет
    ("hurghada", "Hurghada", "EG", "beach", 1),
    ("sharm", "Sharm el-Sheikh", "EG", "beach", 2),
    # Эмираты
    ("dubai", "Dubai", "AE", "beach", 1),
    ("abu-dhabi", "Abu Dhabi", "AE", "city", 2),
    # Юго-Восточная Азия и Индийский океан
    ("phuket", "Phuket", "TH", "beach", 1),
    ("pattaya", "Pattaya", "TH", "beach", 2),
    ("bangkok", "Bangkok", "TH", "city", 2),
    ("da-nang", "Da Nang", "VN", "beach", 2),
    ("nha-trang", "Nha Trang", "VN", "beach", 2),
    ("sanya", "Sanya", "CN", "beach", 2),
    ("male", "Male", "MV", "beach", 1),
    # Кипр
    ("larnaca", "Larnaca", "CY", "beach", 2),
    ("paphos", "Paphos", "CY", "beach", 2),
]


def _norm(s: str) -> str:
    return s.strip().lower().replace("\u2019", "'")


def load_cities() -> dict[tuple[str, str], dict[str, Any]]:
    """{(имя, страна): лучшая строка}. name/asciiname — точный индекс; alternatenames —
    только fallback, иначе «Kemer» схватит чужой город с таким алиасом."""
    import io
    import zipfile

    raw = http_get(GEONAMES_URL, timeout=300)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    member = next(n for n in zf.namelist() if n.endswith(".txt"))
    index: dict[tuple[str, str], dict[str, Any]] = {}
    alt_index: dict[tuple[str, str], dict[str, Any]] = {}

    def put(target: dict, keys: set[str], row: dict) -> None:
        for k in keys:
            cur = target.get((k, row["country"]))
            if cur is None or row["population"] > cur["population"]:
                target[(k, row["country"])] = row

    with zf.open(member) as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            f = line.rstrip("\n").split("\t")
            if len(f) < 19:
                continue
            row = {
                "geoname_id": int(f[0]), "name": f[1], "asciiname": f[2],
                "alternatenames": f[3], "lat": float(f[4]), "lon": float(f[5]),
                "country": f[8], "admin1": f[10], "population": int(f[14] or 0),
                "timezone": f[17],
            }
            put(index, {_norm(row["asciiname"]), _norm(row["name"])}, row)
            alt = {_norm(a) for a in row["alternatenames"].split(",") if a}
            put(alt_index, alt - {_norm(row["asciiname"]), _norm(row["name"])}, row)
    index[("*ALT*", "*ALT*")] = alt_index  # fallback-индекс передаём через спец-ключ
    return index


def find_city(index: dict, query: str, country: str | None) -> dict[str, Any] | None:
    q = _norm(query)
    alt: dict = index.get(("*ALT*", "*ALT*"), {})
    if country is not None:
        return index.get((q, country)) or alt.get((q, country))
    # спорные территории: любой страны, самое крупное совпадение
    best = None
    for src in (index, alt):
        for (name, _cc), row in src.items():
            if name == q and (best is None or row["population"] > best["population"]):
                best = row
    return best


def run() -> list[dict[str, Any]]:
    common.source("geonames_bulk", "GeoNames cities500", GEONAMES_URL, "real")
    index = load_cities()
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for code, query, country, theme, tier in CURATED:
        hit = find_city(index, query, country)
        if hit is None:
            missing.append(f"{code} (запрос: {query})")
            continue
        rows.append({
            "code": code,
            "geoname_id": hit["geoname_id"],
            "name": hit["name"],
            "country_code": "RU" if code in CRIMEA_DISPLAY else hit["country"],
            "country": "Россия" if code in CRIMEA_DISPLAY
            else COUNTRIES.get(hit["country"], hit["country"]),
            "admin1": hit["admin1"],
            "lat": hit["lat"],
            "lon": hit["lon"],
            "population": hit["population"],
            "timezone": hit["timezone"],
            "theme": theme,
            "tier": tier,
            "data_status": "real",
            "source_id": "geonames_bulk",
        })
    if missing:
        print(f"  ! не нашлись в GeoNames: {missing}")
    common.write_seed("destinations.json", rows)
    return rows


if __name__ == "__main__":
    run()
