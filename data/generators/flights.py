"""Перелёты: transport_legs на окно демо.

База — синтетическое расписание поверх РЕАЛЬНЫХ аэропортов (OurAirports IATA/координаты):
длительность из большого круга, вылеты/прилёты в местных таймзонах, часть маршрутов —
red-eye и с пересадкой (на них валидатор проверяет ARRIVAL_AFTER_CHECKIN_DEADLINE и
TRANSFER_GAP_TOO_SHORT).

Amadeus test API подключается, если в окружении есть AMADEUS_CLIENT_KEY/SECRET и сеть до
test.api.amadeus.com. На этой машине DNS заблокирован — без ключей шаг честно пропускается.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import data.generators.common as common
from data.generators.common import haversine_km, pick, randint, unit, jitter

WINDOW_START = dt.date(2026, 11, 1)
WINDOW_END = dt.date(2026, 11, 30)
CRUISE_KMH = 780.0
GROUNDE_MIN = 55  # рулёжка/эшафотирование

# хабы вылета (iata) — берём из airports.json, если нашлись
ORIGINS = ["SVO", "DME", "VKO", "LED"]

DEST_PRICE_FACTOR = {"AE": 1.25, "TR": 1.0, "EG": 1.1, "TH": 1.15, "MV": 1.3,
                     "VN": 1.15, "CN": 1.1, "CY": 1.05, "RU": 0.95}


def _route_legs(origin: dict, gateway: dict, dest: dict, date: dt.date,
                idx: int) -> list[dict[str, Any]]:
    arr_ap = _airports_by_iata[gateway["iata"]]
    dist = haversine_km(origin["lat"], origin["lon"], arr_ap["lat"], arr_ap["lon"])
    flight_min = int(dist / CRUISE_KMH * 60 + GROUNDE_MIN)
    key = f"leg:{origin['iata']}>{gateway['iata']}|{date.isoformat()}|{idx}"

    overnight = unit(key + "|night") < 0.18
    dep_hour = pick(key + "|hour", [8, 9, 11, 13, 15, 17, 20, 22, 23] if not overnight
                    else [0, 1, 2])
    dep_min = pick(key + "|min", [0, 10, 20, 30, 40, 50])
    o_tz = _tz_offset(origin.get("timezone", ""))
    d_tz = _tz_offset(arr_ap.get("timezone") or dest["timezone"])
    dep = dt.datetime.combine(date, dt.time(dep_hour, dep_min))
    arr = dep + dt.timedelta(minutes=flight_min + (d_tz - o_tz) * 60)

    price = (9000 + dist * 7.2) * DEST_PRICE_FACTOR.get(dest["country_code"], 1.0)
    price = round(price * jitter(key + "|p", 0.25) / 100) * 100
    rows = [{
        "leg_id": f"flt-{key.replace(':', '-').replace('|', '-').replace('>', '-')}",
        "date": date.isoformat(),
        "route": f"{origin['iata']}-{gateway['iata']}",
        "dep_iata": origin["iata"],
        "arr_iata": gateway["iata"],
        "dest_code": dest["code"],
        "dep_at": dep.isoformat(timespec="minutes"),
        "dep_tz": origin.get("timezone", ""),
        "arr_at": arr.isoformat(timespec="minutes"),
        "arr_tz": arr_ap.get("timezone") or dest["timezone"],
        "direct": True,
        "duration_min": int((arr - dep).total_seconds() / 60),
        "base_price_rub": price,
        "currency": "RUB",
        "overnight": overnight,
        "data_status": "synthetic",
        "source_id": "synthetic_seed",
    }]
    # раз в неделю — вариант с пересадкой: дешевле, дольше, прилёт позже
    if date.weekday() == 2 and dist > 2500:
        hub = pick(key + "|hub", ["IST", "DXB", "SVO"])
        if hub in _airports_by_iata:
            dep2h = dep.replace(hour=pick(key + "|h2", [6, 10, 14]))
            arr2h = dep2h + dt.timedelta(
                minutes=flight_min + randint(key + "|lay", 120, 320))
            rows.append({
                "leg_id": f"flt-1-{origin['iata']}>{hub}>{gateway['iata']}|{date.isoformat()}",
                "date": date.isoformat(),
                "route": f"{origin['iata']}-{hub}-{gateway['iata']}",
                "dep_iata": origin["iata"],
                "arr_iata": gateway["iata"],
                "dest_code": dest["code"],
                "dep_at": dep2h.isoformat(timespec="minutes"),
                "dep_tz": origin.get("timezone", ""),
                "arr_at": arr2h.isoformat(timespec="minutes"),
                "arr_tz": arr_ap.get("timezone") or dest["timezone"],
                "direct": False,
                "duration_min": int((arr2h - dep2h).total_seconds() / 60),
                "base_price_rub": round(price * 0.82 / 100) * 100,
                "currency": "RUB",
                "overnight": False,
                "data_status": "synthetic",
                "source_id": "synthetic_seed",
            })
    return rows


_airports_by_iata: dict[str, dict] = {}


def _tz_offset(tz_name: str) -> float:
    """Приблизительный часовой сдвиг для порядка прилёта; точность — в duration_min."""
    table = {
        "Europe/Moscow": 3, "Europe/Kaliningrad": 1, "Europe/Simferopol": 3,
        "Europe/Astrakhan": 4, "Europe/Volgograd": 3, "Europe/Samara": 4,
        "Asia/Yekaterinburg": 5, "Asia/Omsk": 6, "Asia/Novosibirsk": 7,
        "Asia/Krasnoyarsk": 7, "Asia/Irkutsk": 8, "Asia/Yakutsk": 9,
        "Asia/Vladivostok": 10, "Asia/Sakhalin": 11, "Asia/Kamchatka": 12,
        "Asia/Magadan": 11, "Europe/Kirov": 3, "Europe/Ulyanovsk": 4,
        "Europe/Saratov": 4, "Asia/Almaty": 6, "Asia/Bishkek": 6, "Asia/Tashkent": 5,
        "Asia/Dubai": 4, "Asia/Bangkok": 7, "Asia/Ho_Chi_Minh": 7, "Asia/Saigon": 7,
        "Asia/Makassar": 8, "Indian/Maldives": 5, "Asia/Yerevan": 4, "Asia/Baku": 4,
        "Europe/Tbilisi": 4, "Europe/Minsk": 3, "Europe/Belgrade": 1,
        "Europe/Istanbul": 3, "Africa/Cairo": 2, "Asia/Hong_Kong": 8,
        "Asia/Shanghai": 8, "Asia/Phuket": 7, "Europe/Nicosia": 2, "Asia/Aden": 3,
    }
    return table.get(tz_name, 3.0)


def _try_amadeus(routes: list[tuple[str, str, dt.date]]) -> list[dict[str, Any]]:
    key = os.environ.get("AMADEUS_CLIENT_KEY")
    secret = os.environ.get("AMADEUS_CLIENT_SECRET")
    if not (key and secret):
        print("  Amadeus: нет AMADEUS_CLIENT_KEY/SECRET — остаёмся на синтетике")
        return []
    import base64
    import urllib.parse
    import urllib.request
    try:
        tok_req = urllib.request.Request(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": key, "client_secret": secret,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(tok_req, timeout=20) as resp:
            token = json.loads(resp.read())["access_token"]
    except Exception as exc:
        print(f"  Amadeus: недоступен ({type(exc).__name__}) — синтетика")
        return []
    offers: list[dict[str, Any]] = []
    for dep, arr, date in routes[:10]:
        url = ("https://test.api.amadeus.com/v2/shopping/flight-offers"
               f"?originLocationCode={dep}&destinationLocationCode={arr}"
               f"&departureDate={date.isoformat()}&adults=2&max=3")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}", "User-Agent": common.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read())
        except Exception:
            continue
        for o in data.get("data", [])[:2]:
            seg = o["itineraries"][0]["segments"][0]
            offers.append({
                "leg_id": f"amd-{o['id']}".replace("/", "-"),
                "date": date.isoformat(),
                "route": f"{dep}-{arr}",
                "dep_iata": dep, "arr_iata": arr,
                "dest_code": None,
                "dep_at": seg["departure"]["at"],
                "arr_at": seg["arrival"]["at"],
                "direct": len(o["itineraries"][0]["segments"]) == 1,
                "duration_min": None,
                "base_price_rub": int(float(o["price"]["grandTotal"])),
                "currency": o["price"]["currency"],
                "overnight": False,
                "data_status": "partially_verified",
                "source_id": "amadeus_test",
            })
    if offers:
        common.source("amadeus_test", "Amadeus Test API",
                      "https://test.api.amadeus.com", "real")
        print(f"  Amadeus: {len(offers)} реальных офферов (тест-среда)")
    return offers


def run() -> None:
    common.source("synthetic_seed", "Детерминированный генератор (hash-синтетика)",
                  "local", "synthetic")
    global _airports_by_iata
    airports = common.read_seed("airports.json")
    _airports_by_iata = {a["iata"]: a for a in airports}
    gateways = common.read_seed("gateways.json")
    dests = {d["code"]: d for d in common.read_seed("destinations.json")}

    # leg привязан к аэропорту, не к городу: направления с общим шлюзом (Крым → SIP)
    # обслуживает один набор маршрутов от репрезентативного направления
    gw_rep: dict[str, tuple[str, dict]] = {}
    for code, g in gateways.items():
        gw_rep.setdefault(g["iata"], (code, g))

    origins = [_airports_by_iata[i] | {"iata": i} for i in ORIGINS if i in _airports_by_iata]
    legs: list[dict[str, Any]] = []
    # Search is keyed by the local *arrival* day (the check-in day), while a
    # long-haul flight can leave Russia on the preceding calendar day. Generate
    # that preceding departure day too, otherwise a perfectly valid first-day
    # package is absent from the catalogue for an implementation artefact.
    date = WINDOW_START - dt.timedelta(days=1)
    while date <= WINDOW_END:
        for o in origins:
            for giata, (code, g) in sorted(gw_rep.items()):
                if giata == o["iata"]:
                    continue
                d = dests[code]
                # из Петербурга летаем только за рубеж и в крупные хабы
                if o["iata"] == "LED" and d["country_code"] == "RU" and d["tier"] != 1:
                    continue
                if o["iata"] in ("DME", "VKO") and d["country_code"] != "RU":
                    continue
                idx = len(legs) % 3
                legs.extend(_route_legs(o, g, d, date, idx))
        date += dt.timedelta(days=1)

    amadeus_routes = [
        (o["iata"], g["iata"], WINDOW_START + dt.timedelta(days=7))
        for o in origins for (_code, g) in gw_rep.values() if o["iata"] != g["iata"]
    ]
    legs.extend(_try_amadeus(amadeus_routes))

    print(f"  transport_legs: {len(legs)} "
          f"(direct={sum(1 for l in legs if l['direct'])}, "
          f"overnight={sum(1 for l in legs if l.get('overnight'))})")
    common.write_seed("transport_legs.json", legs, compact=True)


if __name__ == "__main__":
    run()
