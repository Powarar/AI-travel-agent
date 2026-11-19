"""Typed HTTP payloads for the v1 trip API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.contracts.enums import RunStatus
from shared.contracts.money import Money

from .auth import is_valid_email, normalize_email


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    value: str


class TripCreated(BaseModel):
    trip_id: UUID


class RunAccepted(BaseModel):
    run_id: UUID


class CheckoutCompleted(BaseModel):
    order_id: str
    idempotent_replay: bool
    simulated: Literal[True] = True


class OrderLine(BaseModel):
    kind: str
    count: int
    total: Money


class OrderSnapshot(BaseModel):
    destination: str | None
    country: str | None
    date_start: date | None
    date_end: date | None
    nights: int | None
    guests: int | None
    pets: bool
    origin: str | None
    total: Money | None
    lines: list[OrderLine]


class OrderOut(BaseModel):
    order_id: str
    trip_id: UUID
    created_at: datetime
    simulated: Literal[True] = True
    snapshot: OrderSnapshot | None


class SSEEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal[
        "run_started", "phase_changed", "llm_call", "tool_started",
        "tool_completed", "tool_failed", "question", "candidates",
        "violation", "repair_started", "plan_updated", "answer_text",
        "checkout_completed", "budget_exhausted",
    ]
    data: dict[str, object]


class RunTrace(BaseModel):
    run_id: UUID
    trip_id: UUID
    status: RunStatus
    awaiting: list[str]
    events: list[SSEEvent]


class CredentialsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_and_validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not is_valid_email(normalized):
            raise ValueError("invalid email")
        return normalized


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class LogoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class AuthUser(BaseModel):
    id: UUID
    email: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser


class MeOut(BaseModel):
    id: UUID
    email: str
    created_at: datetime
    telegram_linked: bool
