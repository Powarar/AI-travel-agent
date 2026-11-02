"""Вход валидатора: собранный план. TripState не трогаем (domain-model layer), repair не реализовываем."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import DataStatus
from shared.contracts.money import PriceLine


class StayNight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    price: PriceLine
    availability: bool
    min_nights: int
    source_id: str
    fetched_at: datetime
    data_status: DataStatus
    rate_present: bool = True


class TransportLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg_id: str
    component: str = "transport_out"
    dep_at: datetime
    arr_at: datetime
    duration_h: float
    direct: bool
    overnight: bool
    price: PriceLine | None = None


class TransferLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transfer_id: str
    kind: str                                   # "arrival" | "departure"
    minutes: int
    price: PriceLine | None = None
    pet_ok: bool | None = None
    capacity: int | None = None


class HotelSpecs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hotel_id: str
    pet_friendly: bool
    check_in_deadline: str                      # "14:00"
    capacity: int | None = None                 # None = вместимость не подтверждена
    min_nights: int = 1
    source_id: str | None = None


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_code: str
    nights: int
    adults: int
    children: list[int] = Field(default_factory=list)
    has_pet: bool = False
    party_total: int | None = None               # для scope=per_person
    hotel: HotelSpecs
    stays: list[StayNight] = Field(default_factory=list)
    legs: list[TransportLeg] = Field(default_factory=list)
    transfers: list[TransferLeg] = Field(default_factory=list)
    early_checkin_ok: bool = False
    departure_buffer_min: int = 120
    arrival_buffer_min: int = 30
    stale_after_days: int = 7
    now: datetime | None = None                  # инжектируется для детерминизма тестов
    extra_lines: list[PriceLine] = Field(default_factory=list)

    @property
    def guests(self) -> int:
        return self.adults + len(self.children)
