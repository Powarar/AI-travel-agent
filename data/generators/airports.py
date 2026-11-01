"""Аэропорты: OurAirports (основной, IATA/координаты) + OpenFlights (таймзоны).

Фильтр: регулярные рейсы, есть IATA, тип large/medium/regional. Для каждого направления —
ближайший «шлюз» (gateway) в радиусе 300 км. Хабы вылета: Moscow SVO/DME/VKO, LED.
"""
from __future__ import annotations

import csv
import io
from typing import Any

import data.generators.common as common
from data.generators.common import haversine_km, http_get, http_get_optional

OURAIRPORTS_URL = "https://ourairports.com/data/airports.csv"
OPENFLIGHTS_URL = (
    "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
)
AIRPORT_TYPES = {"large_airport", "medium_airport", "regional_airport"}

# IATA, которые всегда держим в пуле (хабы вылета), даже если далеко от направлений.
ORIGIN_HUBS = {"SVO", "DME", "VKO", "LED", "KZN", "OVB", "SVX", "VVO", "KGF"}

# Страны, аэропорты которых нам вообще интересны (плюс Абхазия/ЮО уйдут как GE/RU).
WANTED_COUNTRIES = {
    "RU", "GE", "AM", "AZ", "KZ", "UZ", "KG", "BY", "RS", "TR", "EG",
    "AE", "TH", "VN", "CN", "MV", "CY", "UA",
}

# Воздушное пространство Крыма закрыто, и OurAirports помечает UKFF как
# scheduled_service=no — но шлюз для трансферов и синтетического расписания нужен.
FORCE_INCLUDE_IDENTS = {"UKFF": "SIP"}


def _tz_from_openflights() -> dict[str, str]:
    raw = http_get_optional(OPENFLIGHTS_URL, timeout=120)
    if raw is None:
        return {}
    tz: dict[str, str] = {}
    # OpenFlights airports.dat: 0 OpenFlights ID,1 name,2 city,3 country,4 IATA,5 ICAO,...,13? timezone is col 14? format:
    # airport_id,name,city,country,iata,icao,latitude,longitude,altitude,time_offset,dst,timezone,country_code
    for row in csv.reader(io.StringIO(raw.decode("utf-8", "ignore"))):
        if len(row) < 13:
            continue
        iata = row[4]
        if len(iata) == 3:
            tz[iata] = row[11]
    return tz


def run() -> dict[str, Any]:
    common.source("ourairports", "OurAirports data dump", OURAIRPORTS_URL, "real")
    common.source("openflights", "OpenFlights airports.dat", OPENFLIGHTS_URL, "real")

    raw = http_get(OURAIRPORTS_URL, timeout=300)
    tz_map = _tz_from_openflights()

    airports: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore")))
    for a in reader:
        iata = (a.get("iata_code") or "").strip()
        forced = FORCE_INCLUDE_IDENTS.get(a.get("ident", ""))
        if forced:
            iata = forced
        if len(iata) != 3 or (a.get("scheduled_service") != "yes" and not forced):
            continue
        if a.get("type") not in AIRPORT_TYPES:
            continue
        if a.get("iso_country") not in WANTED_COUNTRIES and iata not in ORIGIN_HUBS:
            continue
        try:
            lat, lon = float(a["latitude_deg"]), float(a["longitude_deg"])
        except ValueError:
            continue
        rec = {
            "iata": iata,
            "ident": a.get("ident", iata),
            "icao": (a.get("icao_code") or "").strip(),
            "name": a.get("name", iata),
            "municipality": a.get("municipality") or "",
            "country": "RU" if forced else a.get("iso_country", ""),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "type": a.get("type", ""),
            "timezone": tz_map.get(iata, ""),
            "data_status": "real",
            "source_id": "ourairports",
        }
        # крупные аэропорты города важнее мелких: сортировка по типу
        prio = {"large_airport": 2, "medium_airport": 1, "regional_airport": 0}
        cur = airports.get(iata)
        if cur is None or prio.get(rec["type"], 0) > prio.get(cur["type"], 0):
            airports[iata] = rec

    # gateway для каждого направления — ближайший аэропорт СВОЕЙ страны (иначе
    # Ялта «летит» в Синоп через всё Чёрное море)
    dests = common.read_seed("destinations.json")
    gateways: dict[str, dict[str, Any]] = {}
    for d in dests:
        best, best_km = None, None
        for ap in airports.values():
            if ap["country"] != d["country_code"]:
                continue
            km = haversine_km(d["lat"], d["lon"], ap["lat"], ap["lon"])
            if km < (best_km if best_km is not None else 301.0):
                best, best_km = ap, km
        if best:
            gateways[d["code"]] = {
                "iata": best["iata"],
                "dist_km": round(best_km, 1),
                "airport": best["name"],
                "tz": best["timezone"] or d["timezone"],
            }
        else:
            print(f"  ! {d['code']}: нет аэропорта в 300 км — без авиадоступности")

    hub_rows = [airports[i] for i in ORIGIN_HUBS if i in airports]
    print(f"  аэропортов в пуле: {len(airports)}, хабы вылета: {[h['iata'] for h in hub_rows]}")
    common.write_seed("airports.json", list(airports.values()))
    common.write_seed("gateways.json", gateways)
    return {"airports": airports, "gateways": gateways}


if __name__ == "__main__":
    run()
