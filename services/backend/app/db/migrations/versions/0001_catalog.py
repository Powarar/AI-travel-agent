"""Baseline catalog schema for reproducible seed data."""

from alembic import op

revision = "0001_catalog"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = '\nCREATE TABLE IF NOT EXISTS sources (\n  source_id text PRIMARY KEY, name text, url text, kind text, fetched_at timestamptz\n);\nCREATE TABLE IF NOT EXISTS destinations (\n  code text PRIMARY KEY, geoname_id bigint, name text, country_code text, country text,\n  admin1 text, lat double precision, lon double precision, population integer,\n  timezone text, theme text, tier integer, data_status text, source_id text\n);\nCREATE TABLE IF NOT EXISTS airports (\n  iata text PRIMARY KEY, ident text, icao text, name text, municipality text,\n  country text, lat double precision, lon double precision, type text, timezone text,\n  data_status text, source_id text\n);\nCREATE TABLE IF NOT EXISTS gateways (\n  dest_code text PRIMARY KEY REFERENCES destinations(code), iata text,\n  dist_km numeric, airport_name text, tz text\n);\nCREATE TABLE IF NOT EXISTS hotels (\n  hotel_id text PRIMARY KEY, dest_code text REFERENCES destinations(code), name text,\n  stars integer, lat double precision, lon double precision, address text,\n  amenities text[], pet_friendly boolean, min_nights integer, check_in_deadline text,\n  base_price_rub numeric, data_status text, source_id text\n);\nCREATE TABLE IF NOT EXISTS stay_rates (\n  hotel_id text REFERENCES hotels(hotel_id), date date, price_per_night numeric,\n  availability boolean, min_nights integer, currency text, data_status text,\n  source_id text, PRIMARY KEY (hotel_id, date)\n);\nCREATE INDEX IF NOT EXISTS idx_stay_rates_date ON stay_rates (date);\nCREATE TABLE IF NOT EXISTS transport_legs (\n  leg_id text PRIMARY KEY, date date, route text, dep_iata text, arr_iata text,\n  dest_code text, dep_at timestamp, dep_tz text, arr_at timestamp, arr_tz text,\n  direct boolean, duration_min integer, base_price_rub numeric, currency text,\n  overnight boolean, data_status text, source_id text\n);\nCREATE INDEX IF NOT EXISTS idx_transport_legs_date ON transport_legs (date);\nCREATE TABLE IF NOT EXISTS transfers (\n  transfer_id text PRIMARY KEY, dest_code text, hotel_id text, airport_iata text,\n  distance_m integer, duration_s integer, price_rub numeric, pet_ok boolean,\n  capacity integer, currency text, data_status text, source_id text\n);\nCREATE TABLE IF NOT EXISTS climate (\n  dest_code text REFERENCES destinations(code), month integer, t_max numeric,\n  t_min numeric, precip_mm_month numeric, data_status text, source_id text,\n  PRIMARY KEY (dest_code, month)\n);\n'


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in ("climate", "transfers", "transport_legs", "stay_rates",
                  "hotels", "gateways", "airports", "destinations", "sources"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
