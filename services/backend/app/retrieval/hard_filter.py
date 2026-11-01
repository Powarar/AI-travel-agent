"""SQL hard-фильтр отелей (docs/ARCHITECTURE.md §5).

Допустимое множество задаёт только SQL. Векторная оценка потом ранжирует внутри него и
никогда его не расширяет: отель, отсеянный здесь, не возвращается ни при каком сходстве.

Дыра данных, названная явно: в `hotels` нет вместимости номера. Состав группы проверяется
по `transfers.capacity` трансфера этого отеля — план без трансфера, в который группа не
садится, собрать нельзя, так что это настоящее ограничение каталога, а не подстановка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from shared.contracts.request import TripRequest

# Удобства, которые в каталоге лежат отдельной колонкой, а не в `amenities`.
_COLUMN_AMENITIES = {"pet_friendly"}


@dataclass(frozen=True)
class HardFilter:
    start: date
    end: date
    guests: int
    destination: str | None = None
    country: str | None = None
    excluded: tuple[str, ...] = ()
    nights_required: int | None = None
    stars_min: int | None = None
    budget_per_night_max: Decimal | None = None
    must: tuple[str, ...] = ()
    pets: bool = False

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    @classmethod
    def from_request(cls, request: TripRequest, *, country: str | None = None) -> HardFilter:
        start, end = request.dates.start.value, request.dates.end.value
        if start is None or end is None or end <= start:
            raise ValueError("hard filter needs known dates with end > start")
        adults = request.party.adults.value or 0
        children = request.party.children.value or []
        per_night = request.lodging.budget_per_night_max.value
        return cls(
            start=start,
            end=end,
            guests=max(adults + len(children), 1),
            destination=request.direction.explicit.value,
            country=country,
            excluded=tuple(request.direction.excluded.value or ()),
            nights_required=request.nights.value,
            stars_min=request.lodging.stars_min.value,
            budget_per_night_max=per_night.amount if per_night is not None else None,
            must=tuple(sorted(request.lodging.must.value or ())),
            pets=bool(request.party.pets.value),
        )


@dataclass(frozen=True)
class HotelCandidate:
    hotel_id: str
    dest_code: str
    name: str
    stars: int | None
    address: str | None
    amenities: tuple[str, ...]
    pet_friendly: bool
    nights: int
    total_price: Decimal
    max_night_price: Decimal
    currency: str
    data_status: str
    destination_name: str
    transfer_capacity: int
    extra: dict[str, Any] = field(default_factory=dict)


def build_sql(f: HardFilter) -> tuple[str, list[Any]]:
    """Один SELECT: каждое hard-поле — отдельное условие, без пост-фильтра в Python."""
    params: list[Any] = [f.start, f.end, f.nights, f.guests]
    where = [
        "t.capacity >= $4",
        "h.min_nights <= $3",
    ]

    def arg(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if f.destination:
        p = arg(f.destination.strip().lower())
        where.append(f"(lower(d.code) = {p} OR lower(d.name) = {p})")
    if f.country:
        where.append(f"lower(d.country) = {arg(f.country.strip().lower())}")
    if f.excluded:
        p = arg([x.strip().lower() for x in f.excluded])
        where.append(f"NOT (lower(d.code) = ANY({p}) OR lower(d.name) = ANY({p}) OR lower(d.country) = ANY({p}))")
    if f.nights_required is not None:
        # `nights` — hard: даты обязаны дать ровно столько ночей, сколько сказал пользователь.
        where.append(f"$3 = {arg(f.nights_required)}")
    if f.stars_min is not None:
        where.append(f"h.stars >= {arg(f.stars_min)}")
    amenities = [a for a in f.must if a not in _COLUMN_AMENITIES]
    if amenities:
        where.append(f"h.amenities @> {arg(amenities)}::text[]")
    if f.pets or "pet_friendly" in f.must:
        where.append("h.pet_friendly AND t.pet_ok")

    having = [
        # Каждая ночь вилки есть в календаре и доступна; дыра в ставках = отель не подходит.
        "count(*) FILTER (WHERE r.availability) = $3",
        "max(r.min_nights) <= $3",
    ]
    if f.budget_per_night_max is not None:
        having.append(f"max(r.price_per_night) <= {arg(f.budget_per_night_max)}")

    sql = f"""
SELECT h.hotel_id, h.dest_code, h.name, h.stars, h.address, h.amenities, h.pet_friendly,
       h.data_status, d.name AS destination_name, t.capacity AS transfer_capacity,
       sum(r.price_per_night) AS total_price, max(r.price_per_night) AS max_night_price,
       min(r.currency) AS currency
FROM hotels h
JOIN destinations d ON d.code = h.dest_code
JOIN transfers t ON t.hotel_id = h.hotel_id
JOIN stay_rates r ON r.hotel_id = h.hotel_id AND r.date >= $1 AND r.date < $2
WHERE {" AND ".join(where)}
GROUP BY h.hotel_id, d.name, t.capacity
HAVING {" AND ".join(having)}
ORDER BY h.hotel_id
"""
    return sql, params


async def fetch_candidates(conn: Any, f: HardFilter) -> list[HotelCandidate]:
    """`conn` — asyncpg.Connection; тип не импортируется, у asyncpg нет стабов."""
    if f.nights <= 0:
        return []
    sql, params = build_sql(f)
    rows = await conn.fetch(sql, *params)
    return [
        HotelCandidate(
            hotel_id=row["hotel_id"],
            dest_code=row["dest_code"],
            name=row["name"],
            stars=row["stars"],
            address=row["address"],
            amenities=tuple(row["amenities"] or ()),
            pet_friendly=bool(row["pet_friendly"]),
            nights=f.nights,
            total_price=Decimal(row["total_price"]),
            max_night_price=Decimal(row["max_night_price"]),
            currency=row["currency"],
            data_status=row["data_status"],
            destination_name=row["destination_name"],
            transfer_capacity=row["transfer_capacity"],
        )
        for row in rows
    ]
