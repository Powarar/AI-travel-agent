"""Климат направлений: Open-Meteo archive (без ключа), 2020–2024 daily → месячная статистика.

Реальные данные. Итог — climate.json: на каждый месяц средние t_max/t_min/sum осадков.
Отдельно отдаём ноябрьское среднее — hotels.py строит по нему сезонный коэффициент цен.
"""
from __future__ import annotations

from typing import Any

import data.generators.common as common
import json
from data.generators.common import http_get

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
YEARS = (2020, 2024)


def fetch(dest: dict[str, Any]) -> dict[str, Any] | None:
    url = (
        f"{ARCHIVE_URL}?latitude={dest['lat']}&longitude={dest['lon']}"
        f"&start_date={YEARS[0]}-01-01&end_date={YEARS[1]}-12-31"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
        f"&timezone={dest['timezone'].replace('/', '%2F')}"
    )
    raw = common.http_get_optional(url, timeout=60, retries=2)
    if raw is None:
        return None
    data = json.loads(raw)
    daily = data.get("daily") or {}
    if not daily.get("time"):
        return None
    acc: dict[int, dict[str, list[float]]] = {
        m: {"tmax": [], "tmin": [], "p": []} for m in range(1, 13)
    }
    for i, day in enumerate(daily["time"]):
        month = int(day[5:7])
        tmax = daily["temperature_2m_max"][i]
        tmin = daily["temperature_2m_min"][i]
        prec = daily["precipitation_sum"][i]
        if tmax is None or tmin is None:
            continue
        acc[month]["tmax"].append(tmax)
        acc[month]["tmin"].append(tmin)
        acc[month]["p"].append(prec or 0.0)
    out: dict[str, Any] = {}
    for m, a in acc.items():
        if not a["tmax"]:
            continue
        out[str(m)] = {
            "t_max": round(sum(a["tmax"]) / len(a["tmax"]), 1),
            "t_min": round(sum(a["tmin"]) / len(a["tmin"]), 1),
            "precip_mm_month": round(sum(a["p"]) / len(a["p"]) * 30, 0),
        }
    return out


def nov_mean(climate_by_month: dict[str, Any]) -> float | None:
    m = climate_by_month.get("11")
    if not m:
        return None
    return (m["t_max"] + m["t_min"]) / 2


def run() -> list[dict[str, Any]]:
    common.source("open_meteo_archive", "Open-Meteo historical archive", ARCHIVE_URL, "real")
    dests = common.read_seed("destinations.json")
    rows: list[dict[str, Any]] = []
    nov: dict[str, float] = {}
    for i, d in enumerate(dests):
        stats = fetch(d)
        if stats is None:
            print(f"  ! климат пропущен: {d['code']}")
            continue
        for month, s in stats.items():
            rows.append({
                "dest_code": d["code"],
                "month": int(month),
                **s,
                "data_status": "real",
                "source_id": "open_meteo_archive",
            })
        n = nov_mean(stats)
        if n is not None:
            nov[d["code"]] = n
        if (i + 1) % 10 == 0:
            print(f"  климат: {i + 1}/{len(dests)}")
    common.write_seed("climate.json", rows)
    common.write_seed("climate_november.json", nov)
    return rows


if __name__ == "__main__":
    run()
