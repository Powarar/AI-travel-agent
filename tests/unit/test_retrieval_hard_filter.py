"""T-10: SQL hard-фильтр на Postgres. Строки теста живут в транзакции и откатываются."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from services.backend.app.ranking import rank
from services.backend.app.retrieval import HardFilter, fetch_candidates

START = date(2026, 11, 14)
END = date(2026, 11, 17)  # 3 ночи


@pytest_asyncio.fixture
async def conn() -> AsyncIterator[Any]:
    dsn = os.environ.get("DATABASE_URL", "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok")
    try:
        connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    tx = connection.transaction()
    await tx.start()
    try:
        await _seed(connection)
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


HOTELS = [
    # hotel_id, dest, stars, amenities, pet_friendly, min_nights, capacity, price, gap
    ("t10-small", "t10-sea", 5, ["pool", "spa"], True, 1, 4, 3000, False),
    ("t10-big", "t10-sea", 3, ["pool"], True, 1, 7, 5000, False),
    ("t10-gap", "t10-sea", 4, ["pool"], True, 1, 7, 2000, True),
    ("t10-long", "t10-sea", 4, ["pool"], True, 5, 7, 2500, False),
    ("t10-nopets", "t10-sea", 4, ["pool"], False, 1, 7, 2600, False),
    ("t10-pricey", "t10-sea", 5, ["pool", "spa"], True, 1, 7, 20000, False),
    ("t10-mountain", "t10-hill", 4, ["spa"], True, 1, 7, 4000, False),
]


async def _seed(conn: Any) -> None:
    now = "now()"
    await conn.execute(
        f"INSERT INTO destinations (code, name, country, fetched_at, synthetic) VALUES "
        f"('t10-sea', 'T10 Sea', 'Т10ленд', {now}, true), ('t10-hill', 'T10 Hill', 'Т10ленд', {now}, true)"
    )
    for hid, dest, stars, amenities, pets, min_n, cap, price, gap in HOTELS:
        await conn.execute(
            "INSERT INTO hotels (hotel_id, dest_code, name, stars, amenities, pet_friendly, min_nights, "
            "data_status, fetched_at, synthetic) VALUES ($1,$2,$3,$4,$5,$6,$7,'synthetic',now(),true)",
            hid, dest, hid, stars, amenities, pets, min_n,
        )
        await conn.execute(
            "INSERT INTO transfers (transfer_id, hotel_id, dest_code, capacity, pet_ok, fetched_at, synthetic) "
            "VALUES ($1,$2,$3,$4,true,now(),true)",
            f"trf-{hid}", hid, dest, cap,
        )
        for i in range((END - START).days):
            if gap and i == 1:
                continue  # дыра в календаре ставок
            await conn.execute(
                "INSERT INTO stay_rates (hotel_id, date, price_per_night, availability, min_nights, currency, "
                "data_status, fetched_at, synthetic) VALUES ($1,$2,$3,true,1,'RUB','synthetic',now(),true)",
                hid, START + timedelta(days=i), Decimal(price),
            )


def _f(**kw: Any) -> HardFilter:
    return HardFilter(**{"start": START, "end": END, "guests": 2, "country": "Т10ленд", **kw})


async def _ids(conn: Any, **kw: Any) -> set[str]:
    return {c.hotel_id for c in await fetch_candidates(conn, _f(**kw))}


@pytest.mark.asyncio
async def test_hotel_dropped_by_guests_never_reaches_top(conn: Any) -> None:
    candidates = await fetch_candidates(conn, _f(guests=5))
    ids = {c.hotel_id for c in candidates}
    assert "t10-small" not in ids and "t10-big" in ids
    # Даже максимальное сходство отсеянного отеля не возвращает его в выдачу.
    ranked = rank(candidates, {"t10-small": 1.0, "t10-big": 0.0})
    assert "t10-small" not in {r.hotel.hotel_id for r in ranked}


@pytest.mark.asyncio
async def test_each_hard_field_is_a_sql_condition(conn: Any) -> None:
    base = await _ids(conn)
    assert "t10-gap" not in base          # нет ставки на одну из ночей
    assert "t10-long" not in base         # min_nights 5 > 3 ночей
    assert {"t10-small", "t10-big", "t10-nopets", "t10-pricey", "t10-mountain"} <= base

    assert await _ids(conn, destination="t10 hill") == {"t10-mountain"}
    assert "t10-mountain" not in await _ids(conn, excluded=("t10-hill",))
    assert await _ids(conn, stars_min=5) == {"t10-small", "t10-pricey"}
    assert "t10-pricey" not in await _ids(conn, budget_per_night_max=Decimal(10000))
    assert await _ids(conn, must=("pool", "spa")) == {"t10-small", "t10-pricey"}
    assert "t10-nopets" not in await _ids(conn, pets=True)
    assert await _ids(conn, nights_required=4) == set()


@pytest.mark.asyncio
async def test_candidate_price_is_sum_of_nightly_rates(conn: Any) -> None:
    [big] = [c for c in await fetch_candidates(conn, _f()) if c.hotel_id == "t10-big"]
    assert big.total_price == Decimal(15000) and big.max_night_price == Decimal(5000)
    assert big.nights == 3 and big.currency == "RUB"
