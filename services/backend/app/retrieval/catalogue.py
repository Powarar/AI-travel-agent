"""Async PostgreSQL sources using the shared deterministic offer converters.

Only the requested service's tables are read. No seed files or provider calls are
used in the HTTP request path. Snapshot binding uses ContextVar, never globals.
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from services.agent.app.orchestration.nodes import SearchResult
from services.agent.app.tools import seed_sources as converters
from .hybrid import hybrid_search

TABLES: dict[str, tuple[str, ...]] = {
    "suggest_destinations": ("destinations", "airports", "transport_legs", "hotels", "stay_rates", "transfers", "climate"),
    "weather_climate": ("destinations", "climate"),
    "transport_search": ("destinations", "airports", "transport_legs", "gateways"),
    "hotel_search": ("destinations", "hotels", "stay_rates"),
    "transfer_search": ("destinations", "transfers"),
}


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok").replace(
        "postgresql+asyncpg://", "postgresql://", 1)


def _row(row: Any) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else value
            for key, value in dict(row).items()}


async def snapshot(conn: Any, name: str, args: Mapping[str, object]) -> converters._Catalog:
    data: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES[name]:
        sql = f"SELECT * FROM {table}"  # table is from a fixed allowlist
        values: list[Any] = []
        if table == "stay_rates" and args.get("dates.start") and args.get("dates.end"):
            sql += " WHERE date >= $1 AND date < $2"
            values = [date.fromisoformat(str(args["dates.start"])), date.fromisoformat(str(args["dates.end"]))]
        if table in {"hotels", "transfers", "transport_legs"} and args.get("destinations"):
            sql += " WHERE dest_code = ANY($1::text[])"
            values = [args["destinations"]]
        data[table] = [_row(row) for row in await conn.fetch(sql, *values)]
    sources = await conn.fetch("SELECT source_id, fetched_at FROM sources")
    destinations = data.get("destinations", [])
    return converters._Catalog(
        destinations=destinations,
        destination_tz={str(row["code"]): str(row.get("timezone") or "") for row in destinations},
        climate={(str(row["dest_code"]), int(row["month"])): row for row in data.get("climate", [])},
        legs=data.get("transport_legs", []), hotels=data.get("hotels", []),
        rates={(str(row["hotel_id"]), str(row["date"])): row for row in data.get("stay_rates", [])},
        transfers=data.get("transfers", []),
        gateways={str(row["dest_code"]): row for row in data.get("gateways", [])},
        airports=data.get("airports", []),
        fetched={str(row["source_id"]): row["fetched_at"] for row in sources if row["fetched_at"] is not None},
    )


class DatabaseSources:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, name: str, args: Mapping[str, object]) -> SearchResult:
        started = time.monotonic()
        conn = await asyncpg.connect(database_url())
        try:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                data = await snapshot(conn, name, args)
                token = converters.catalog_override.set(data)
                try:
                    result: SearchResult = getattr(converters, name)(args)
                finally:
                    converters.catalog_override.reset(token)
            if name == "hotel_search":
                nights = int(str(args.get("nights") or 0))
                admissible = [hotel.specs.hotel_id for hotel in result.hotels
                              if len(hotel.stays) == nights and nights > 0
                              and all(stay.availability and stay.min_nights <= nights for stay in hotel.stays)]
                preferences = args.get("soft_preferences") or []
                query = " ".join(str(p) for p in preferences) if isinstance(preferences, list) else str(preferences)
                must = args.get("lodging.must") or []
                if isinstance(must, list):
                    from .index_text import AMENITY_RU
                    query = " ".join([query, *(AMENITY_RU.get(str(item), str(item)) for item in must)]).strip()
                scores, mode = await hybrid_search(conn, query, admissible)
                result = result.model_copy(update={
                    "hotels": [hotel.model_copy(update={"score": scores.get(hotel.specs.hotel_id, 0)})
                               for hotel in result.hotels],
                    "meta": {**result.meta, "retrieval_mode": mode},
                })
            self.calls.append({"tool": name, "ms": round((time.monotonic() - started) * 1000), "status": "ok"})
            return result
        finally:
            await conn.close()

    def sources(self) -> dict[str, Callable[[Mapping[str, object]], Awaitable[SearchResult]]]:
        def bind(name: str) -> Callable[[Mapping[str, object]], Awaitable[SearchResult]]:
            async def source(args: Mapping[str, object]) -> SearchResult:
                return await self.call(name, args)
            return source
        return {name: bind(name) for name in TABLES}


def database_sources() -> dict[str, Any]:
    return DatabaseSources().sources()
