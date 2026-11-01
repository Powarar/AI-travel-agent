"""Загрузка data/seeds/*.json в Postgres.

Stdlib-only: SQL-поток (DDL + COPY FROM STDIN) уходит в `psql` внутри контейнера postgres —
не нужны ни psycopg, ни собранный backend-образ. Идемпотентно: таблицы пересоздаются,
данные перезаливаются целиком (dev-сид, не прод).

Запуск:  python3 scripts/seed_database.py
         python3 scripts/seed_database.py --emit-sql /tmp/seed.sql   # без docker, проверить SQL
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "data" / "seeds"

DDL = """
CREATE TABLE IF NOT EXISTS sources (
  source_id text PRIMARY KEY, name text, url text, kind text, fetched_at timestamptz
);
CREATE TABLE IF NOT EXISTS destinations (
  code text PRIMARY KEY, geoname_id bigint, name text, country_code text, country text,
  admin1 text, lat double precision, lon double precision, population integer,
  timezone text, theme text, tier integer, data_status text, source_id text,
  fetched_at timestamptz, synthetic boolean
);
CREATE TABLE IF NOT EXISTS airports (
  iata text PRIMARY KEY, ident text, icao text, name text, municipality text,
  country text, lat double precision, lon double precision, type text, timezone text,
  data_status text, source_id text, fetched_at timestamptz, synthetic boolean
);
CREATE TABLE IF NOT EXISTS gateways (
  dest_code text PRIMARY KEY REFERENCES destinations(code), iata text,
  dist_km numeric, airport_name text, tz text
);
CREATE TABLE IF NOT EXISTS hotels (
  hotel_id text PRIMARY KEY, dest_code text REFERENCES destinations(code), name text,
  stars integer, lat double precision, lon double precision, address text,
  amenities text[], pet_friendly boolean, min_nights integer, check_in_deadline text,
  base_price_rub numeric, data_status text, source_id text,
  fetched_at timestamptz, synthetic boolean
);
CREATE TABLE IF NOT EXISTS stay_rates (
  hotel_id text REFERENCES hotels(hotel_id), date date, price_per_night numeric,
  availability boolean, min_nights integer, currency text, data_status text,
  source_id text, fetched_at timestamptz, synthetic boolean,
  PRIMARY KEY (hotel_id, date)
);
CREATE INDEX IF NOT EXISTS idx_stay_rates_date ON stay_rates (date);
CREATE TABLE IF NOT EXISTS transport_legs (
  leg_id text PRIMARY KEY, date date, route text, dep_iata text, arr_iata text,
  dest_code text, dep_at timestamp, dep_tz text, arr_at timestamp, arr_tz text,
  direct boolean, duration_min integer, base_price_rub numeric, currency text,
  overnight boolean, data_status text, source_id text,
  fetched_at timestamptz, synthetic boolean
);
CREATE INDEX IF NOT EXISTS idx_transport_legs_date ON transport_legs (date);
CREATE TABLE IF NOT EXISTS transfers (
  transfer_id text PRIMARY KEY, dest_code text, hotel_id text, airport_iata text,
  distance_m integer, duration_s integer, price_rub numeric, pet_ok boolean,
  capacity integer, currency text, data_status text, source_id text,
  fetched_at timestamptz, synthetic boolean
);
CREATE TABLE IF NOT EXISTS climate (
  dest_code text REFERENCES destinations(code), month integer, t_max numeric,
  t_min numeric, precip_mm_month numeric, data_status text, source_id text,
  fetched_at timestamptz, synthetic boolean,
  PRIMARY KEY (dest_code, month)
);
"""

ORDER = ["sources", "destinations", "airports", "gateways", "hotels", "stay_rates",
         "transport_legs", "transfers", "climate"]


def _val(v: Any, col: str) -> str:
    if v is None:
        return "\\N"
    if col == "amenities":
        return "{" + ",".join(str(x) for x in v) + "}"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, float):
        return repr(v)
    s = str(v)
    return s.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")


def _load(name: str) -> list[dict]:
    path = SEEDS / name
    if not path.exists():
        sys.exit(f"нет {path.relative_to(ROOT)} — сначала прогони data/generators/run_all.py")
    return json.loads(path.read_text())


def rows_gateways() -> list[dict]:
    g = _load("gateways.json")
    return [
        {"dest_code": code, "iata": v["iata"], "dist_km": v["dist_km"],
         "airport_name": v["airport"], "tz": v["tz"]}
        for code, v in sorted(g.items())
    ]


TABLES: dict[str, tuple[str, list[str]]] = {
    "sources": ("sources.json", ["source_id", "name", "url", "kind", "fetched_at"]),
    "destinations": ("destinations.json",
                     ["code", "geoname_id", "name", "country_code", "country", "admin1",
                      "lat", "lon", "population", "timezone", "theme", "tier",
                      "data_status", "source_id", "fetched_at", "synthetic"]),
    "airports": ("airports.json",
                 ["iata", "ident", "icao", "name", "municipality", "country", "lat",
                  "lon", "type", "timezone", "data_status", "source_id",
                  "fetched_at", "synthetic"]),
    "gateways": (None, ["dest_code", "iata", "dist_km", "airport_name", "tz"]),
    "hotels": ("hotels.json",
               ["hotel_id", "dest_code", "name", "stars", "lat", "lon", "address",
                "amenities", "pet_friendly", "min_nights", "check_in_deadline",
                "base_price_rub", "data_status", "source_id", "fetched_at", "synthetic"]),
    "stay_rates": ("stay_rates.json",
                   ["hotel_id", "date", "price_per_night", "availability", "min_nights",
                    "currency", "data_status", "source_id", "fetched_at", "synthetic"]),
    "transport_legs": ("transport_legs.json",
                       ["leg_id", "date", "route", "dep_iata", "arr_iata", "dest_code",
                        "dep_at", "dep_tz", "arr_at", "arr_tz", "direct", "duration_min",
                        "base_price_rub", "currency", "overnight", "data_status",
                        "source_id", "fetched_at", "synthetic"]),
    "transfers": ("transfers.json",
                  ["transfer_id", "dest_code", "hotel_id", "airport_iata", "distance_m",
                   "duration_s", "price_rub", "pet_ok", "capacity", "currency",
                   "data_status", "source_id", "fetched_at", "synthetic"]),
    "climate": ("climate.json",
                ["dest_code", "month", "t_max", "t_min", "precip_mm_month",
                 "data_status", "source_id", "fetched_at", "synthetic"]),
}


def build_sql() -> str:
    parts = ["BEGIN;", "SET client_min_messages = warning;", DDL,
             "TRUNCATE " + ", ".join(ORDER) + " CASCADE;"]
    for table in ORDER:
        fname, cols = TABLES[table]
        rows = rows_gateways() if table == "gateways" else _load(fname)
        parts.append(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT text);")
        for r in rows:
            parts.append("\t".join(_val(r.get(c), c) for c in cols))
        parts.append("\\.")
        print(f"  {table}: {len(rows)} строк", file=sys.stderr)
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


COUNTS_SQL = """
SELECT 'destinations', count(*) FROM destinations
UNION ALL SELECT 'airports', count(*) FROM airports
UNION ALL SELECT 'hotels', count(*) FROM hotels
UNION ALL SELECT 'stay_rates', count(*) FROM stay_rates
UNION ALL SELECT 'transport_legs', count(*) FROM transport_legs
UNION ALL SELECT 'transfers', count(*) FROM transfers
UNION ALL SELECT 'climate', count(*) FROM climate
UNION ALL SELECT 'sources', count(*) FROM sources;
"""

STATUS_SQL = """
SELECT 'hotels', data_status, count(*) FROM hotels GROUP BY 2
UNION ALL SELECT 'transfers', data_status, count(*) FROM transfers GROUP BY 2;
"""


def main() -> int:
    sql = build_sql()
    if "--emit-sql" in sys.argv:
        out = sys.argv[sys.argv.index("--emit-sql") + 1]
        Path(out).write_text(sql)
        print(f"SQL записан в {out} ({len(sql)/1e6:.1f} МБ)")
        return 0

    psql = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "exec", "-T",
            "postgres", "psql", "-U", "ostrovok", "-d", "ostrovok",
            "-v", "ON_ERROR_STOP=1", "-q"]
    try:
        subprocess.run(["docker", "compose", "up", "-d", "postgres"], cwd=ROOT, check=True)
        subprocess.run(psql, input=sql.encode(), check=True)
    except FileNotFoundError:
        sys.exit("нет docker — используй --emit-sql /tmp/seed.sql")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"psql завершился с ошибкой (см. вывод выше): {exc.returncode}")

    for q in (COUNTS_SQL, STATUS_SQL):
        subprocess.run(psql + ["-c", q], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
