"""Трансферы: OSRM demo server, матрица аэропорт→отели (1 запрос на направление).

Длительность и расстояние — реальные (data_status=real). Цена такси — синтетическая
формула по стране. Если OSRM недоступен — оценка по дистанции большого круга ×1.35 /
45 км/ч, и строка честно помечена synthetic.

Демо-сервер OSRM агрессивно лимитирует: между запросами пауза, ошибки — ретрай с backoff.
"""
from __future__ import annotations

import json
import time
from typing import Any

import data.generators.common as common
from data.generators.common import chunked, haversine_km, jitter, unit

OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving"
MAX_CHUNK = 40   # координат на запрос (демо-сервер держит ~40)
PAUSE_S = 1.2    # демо-сервер режет частые запросы


def _table(airport: dict, hotels: list[dict]) -> tuple[list[float], list[int]] | None:
    coords = f"{airport['lon']:.5f},{airport['lat']:.5f};" + ";".join(
        f"{h['lon']:.5f},{h['lat']:.5f}" for h in hotels)
    url = f"{OSRM_TABLE}/{coords}?sources=0&annotations=duration,distance"
    raw = common.http_get_optional(url, timeout=60, retries=3)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if data.get("code") != "Ok":
            return None
        # durations/distances — матрицы source→destination; при sources=0 одна строка.
        # (destinations[i]["distance"] — это снап к дороге, не маршрут, его не берём.)
        return data["durations"][0], data["distances"][0]
    except (KeyError, IndexError, ValueError):
        return None


def _price_rub(cc: str, km: float) -> int:
    if cc == "RU":
        return round((350 + 32 * km) / 50) * 50
    return round((500 + 48 * km) / 50) * 50  # туристическое такси за рубежом дороже


def run() -> None:
    common.source("osrm_demo", "OSRM public demo server", OSRM_TABLE, "real")
    common.source("synthetic_seed", "Детерминированный генератор (hash-синтетика)",
                  "local", "synthetic")
    airports_by_iata = {a["iata"]: a for a in common.read_seed("airports.json")}
    gateways = common.read_seed("gateways.json")
    dests = {d["code"]: d for d in common.read_seed("destinations.json")}
    hotels = common.read_seed("hotels.json")
    by_dest: dict[str, list[dict]] = {}
    for h in hotels:
        by_dest.setdefault(h["dest_code"], []).append(h)

    rows: list[dict[str, Any]] = []
    osrm_ok = osrm_fail = 0
    for code, g in gateways.items():
        airport = airports_by_iata.get(g["iata"]) or dests[code]
        dest_hotels = by_dest.get(code, [])
        for chunk in chunked(dest_hotels, MAX_CHUNK - 1):
            time.sleep(PAUSE_S)
            table = _table(airport, chunk)
            for j, h in enumerate(chunk):
                key = f"tr:{h['hotel_id']}"
                if table is not None:
                    # колонка 0 — аэропорт сам к себе, отели со сдвигом +1
                    dur_s, dist_m = table[0][j + 1], table[1][j + 1]
                    if dur_s is None:
                        dur_s = dist_m = None
                    elif dist_m / 1000 / max(dur_s / 3600, 1e-9) < 12 or dist_m > 450_000:
                        # OSRM demo иногда даёт горный детур с абсурдной средней
                        # скоростью — берём геометрию, честно помечаем synthetic
                        dur_s = dist_m = None
                else:
                    dur_s = dist_m = None
                if dist_m is not None:
                    source_id, status = "osrm_demo", "real"
                    osrm_ok += 1
                else:
                    km = haversine_km(airport["lat"], airport["lon"], h["lat"], h["lon"]) * 1.35
                    dist_m, dur_s = int(km * 1000), int(km / 45 * 3600)
                    source_id, status = "synthetic_seed", "synthetic"
                    osrm_fail += 1
                km = dist_m / 1000
                rows.append({
                    "transfer_id": f"trf-{h['hotel_id']}",
                    "dest_code": code,
                    "hotel_id": h["hotel_id"],
                    "airport_iata": g["iata"],
                    "distance_m": int(round(dist_m)),
                    "duration_s": int(round(dur_s)),
                    "price_rub": _price_rub(dests[code]["country_code"], km),
                    "pet_ok": unit(key + "|pet") < 0.4,
                    "capacity": 4 if unit(key + "|cap") < 0.85 else 7,
                    "currency": "RUB",
                    "data_status": status,
                    "source_id": source_id,
                })
        if len(rows) % 200 < len(dest_hotels):
            print(f"  трансферы: {code} готово (osrm ok={osrm_ok})")

    print(f"  трансферы: {len(rows)} строк; OSRM: {osrm_ok} real, {osrm_fail} fallback")
    common.write_seed("transfers.json", rows, compact=True)


if __name__ == "__main__":
    run()
